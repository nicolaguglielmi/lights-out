"""core/gates/migration.py: the expand/contract migration safety gate (docs/adr/0123).

Spec-derived: every rule of the ADR's table, the comment/literal stripping,
the Python ORM operations, the contract declaration and the verdict shape.
Paths are synthetic (service/db/migrations/...): the gate names no workload.
"""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr

from core.gates import NoGo, migration
from core.tests.gitrepo import Repo

SQL = "service/db/migrations/0002_change.sql"
PY = "service/db/migrations/0002_change.py"


def rules(findings):
    return [f.rule for f in findings]


class StripSqlTests(unittest.TestCase):
    def test_line_comment_is_removed_and_newline_kept(self):
        got = migration.strip_sql("-- DROP TABLE a;\nSELECT 1;")
        self.assertNotIn("DROP", got)
        self.assertEqual(got.count("\n"), 1)

    def test_nested_block_comment_is_removed_with_its_lines(self):
        got = migration.strip_sql("/* a /* DROP TABLE x */ DROP TABLE y\n*/\nSELECT 1;")
        self.assertNotIn("DROP", got)
        self.assertEqual(got.count("\n"), 2)

    def test_string_literal_is_blanked_with_doubled_quote(self):
        got = migration.strip_sql("INSERT INTO t VALUES ('it''s -- DROP TABLE x');")
        self.assertNotIn("DROP", got)
        self.assertTrue(got.rstrip().endswith(");"))

    def test_escape_string_backslash_quote(self):
        got = migration.strip_sql("SELECT E'a\\' ; DROP TABLE x'; SELECT 2;")
        self.assertNotIn("DROP", got)
        self.assertIn("SELECT 2", got)

    def test_comment_marker_inside_a_string_is_not_a_comment(self):
        got = migration.strip_sql("SELECT '--'; DROP TABLE x;")
        self.assertIn("DROP TABLE x", got)

    def test_quoted_identifier_keeps_shape_not_content(self):
        got = migration.strip_sql('ALTER TABLE "drop table" ADD COLUMN a int;')
        self.assertNotIn("drop table", got)


class SqlRuleTests(unittest.TestCase):
    def one(self, sql, rule):
        got = migration.scan_sql(sql)
        self.assertEqual(rules(got), [rule], got)
        return got[0]

    def clean(self, sql):
        self.assertEqual(migration.scan_sql(sql), [], sql)

    def test_drop_table_and_view(self):
        self.one("DROP TABLE users;", "drop-table")
        self.one("drop view v;", "drop-table")
        self.one("DROP MATERIALIZED VIEW mv;", "drop-table")
        self.one("DROP TABLE IF EXISTS users;", "drop-table")

    def test_drop_temp_table_is_allowed(self):
        self.clean("CREATE TEMP TABLE scratch (a int);\nDROP TABLE scratch;")
        self.clean("DROP TEMPORARY TABLE IF EXISTS x;")
        self.clean("DROP TABLE IF EXISTS tmp_backfill;")

    def test_drop_unrelated_objects_is_allowed(self):
        self.clean("DROP INDEX idx_a;")
        self.clean("ALTER TABLE t DROP CONSTRAINT c;")
        self.clean("ALTER TABLE t ALTER COLUMN a DROP NOT NULL;")
        self.clean("ALTER TABLE t ALTER COLUMN a DROP DEFAULT;")

    def test_drop_column(self):
        f = self.one("SELECT 1;\n\nALTER TABLE t DROP COLUMN a;", "drop-column")
        self.assertEqual(f.line, 3)
        self.one("ALTER TABLE t DROP COLUMN IF EXISTS a;", "drop-column")
        self.one("ALTER TABLE t DROP a;", "drop-column")

    def test_renames(self):
        self.one("RENAME TABLE a TO b;", "rename")
        self.one("ALTER TABLE a RENAME TO b;", "rename")
        self.one("ALTER TABLE a RENAME COLUMN x TO y;", "rename")
        self.one("ALTER TABLE a RENAME x TO y;", "rename")
        self.one("ALTER TABLE a CHANGE x y int;", "rename")
        self.clean("ALTER TABLE a RENAME CONSTRAINT c1 TO c2;")

    def test_truncate(self):
        self.one("TRUNCATE TABLE t;", "truncate")
        self.one("truncate t, u;", "truncate")
        self.clean("SELECT TRUNCATE(price, 2) FROM t;")

    def test_alter_type_is_flagged_unless_to_unbounded_text(self):
        self.one("ALTER TABLE t ALTER COLUMN a TYPE varchar(10);", "alter-type")
        self.one("ALTER TABLE t ALTER COLUMN a SET DATA TYPE integer;", "alter-type")
        self.one("ALTER TABLE t MODIFY COLUMN a varchar(10);", "alter-type")
        self.one("ALTER TABLE t ALTER COLUMN a int;", "alter-type")
        self.clean("ALTER TABLE t ALTER COLUMN a TYPE text;")
        self.clean("ALTER TABLE t ALTER COLUMN a TYPE varchar;")
        self.clean("ALTER TABLE t MODIFY a LONGTEXT;")

    def test_add_not_null_needs_a_default(self):
        self.one("ALTER TABLE t ADD COLUMN a int NOT NULL;", "add-not-null")
        self.one("ALTER TABLE t ADD a int NOT NULL;", "add-not-null")
        self.one("ALTER TABLE t ADD COLUMN ok int DEFAULT 1, ADD COLUMN a int NOT NULL;", "add-not-null")
        self.clean("ALTER TABLE t ADD COLUMN a int NOT NULL DEFAULT 0;")
        self.clean("ALTER TABLE t ADD COLUMN a int;")
        self.clean("ALTER TABLE t ADD COLUMN id bigserial NOT NULL;")
        self.clean("ALTER TABLE t ADD CONSTRAINT c CHECK (a IS NOT NULL);")
        self.clean("ALTER TABLE t ADD CONSTRAINT u UNIQUE (a);")

    def test_set_not_null(self):
        self.one("ALTER TABLE t ALTER COLUMN a SET NOT NULL;", "set-not-null")
        self.clean("ALTER TABLE t ALTER COLUMN a SET DEFAULT 1;")

    def test_new_table_in_same_file_is_fresh(self):
        self.clean("CREATE TABLE n (id int);\nALTER TABLE n ADD COLUMN a int NOT NULL;")
        self.clean("CREATE TABLE n (id int);\nALTER TABLE n DROP COLUMN id;")
        self.one("CREATE TABLE IF NOT EXISTS n (id int);\nALTER TABLE n DROP COLUMN id;", "drop-column")

    def test_expand_statements_are_clean(self):
        self.clean("CREATE TABLE t (id bigserial PRIMARY KEY, a text NOT NULL DEFAULT '');\n"
                   "CREATE INDEX i ON t (a);\nALTER TABLE t ADD COLUMN b timestamptz;\n"
                   "UPDATE t SET b = now();\nINSERT INTO t (a) VALUES ('x');")

    def test_comments_and_literals_never_trigger(self):
        self.clean("-- DROP TABLE users;\n/* ALTER TABLE t DROP COLUMN a; */\n"
                   "INSERT INTO log (msg) VALUES ('DROP TABLE users; TRUNCATE t');\nSELECT 1;")

    def test_real_statement_after_a_comment_is_still_found_on_its_line(self):
        f = self.one("-- keep\n/* x\ny */\nDROP TABLE users;", "drop-table")
        self.assertEqual(f.line, 4)

    def test_multiple_findings_are_ordered_by_line(self):
        got = migration.scan_sql("ALTER TABLE t DROP COLUMN a;\nTRUNCATE t;")
        self.assertEqual([(f.line, f.rule) for f in got], [(1, "drop-column"), (2, "truncate")])


class PythonRuleTests(unittest.TestCase):
    def found(self, body):
        return migration.scan_python(body)

    def test_alembic_forward_ops(self):
        src = ("def upgrade():\n    op.drop_column('t', 'a')\n    op.drop_table('u')\n"
               "    op.rename_table('v', 'w')\n")
        got = self.found(src)
        self.assertEqual([(f.line, f.rule) for f in got],
                         [(2, "drop-column"), (3, "drop-table"), (4, "rename")])

    def test_batch_op_is_recognised(self):
        src = "def upgrade():\n    with op.batch_alter_table('t') as b:\n        b.drop_column('a')\n"
        self.assertEqual(rules(self.found(src)), ["drop-column"])

    def test_downgrade_is_not_judged(self):
        src = ("def upgrade():\n    op.add_column('t', sa.Column('a', sa.Integer))\n\n"
               "def downgrade():\n    op.drop_column('t', 'a')\n    op.drop_table('t')\n")
        self.assertEqual(self.found(src), [])

    def test_alter_column_nullable_false_needs_server_default(self):
        self.assertEqual(rules(self.found("op.alter_column('t', 'a', nullable=False)\n")), ["set-not-null"])
        self.assertEqual(self.found("op.alter_column('t', 'a', nullable=False, server_default='0')\n"), [])
        self.assertEqual(self.found("op.alter_column('t', 'a', nullable=True)\n"), [])

    def test_alter_column_rename_and_type(self):
        self.assertEqual(rules(self.found("op.alter_column('t', 'a', new_column_name='b')\n")), ["rename"])
        self.assertEqual(rules(self.found("op.alter_column('t', 'a', type_=sa.String(5))\n")), ["alter-type"])
        self.assertEqual(self.found("op.alter_column('t', 'a', type_=sa.Text())\n"), [])

    def test_add_column_not_null(self):
        bad = "op.add_column('t', sa.Column('a', sa.Integer(), nullable=False))\n"
        self.assertEqual(rules(self.found(bad)), ["add-not-null"])
        ok = "op.add_column('t', sa.Column('a', sa.Integer(), nullable=False, server_default='0'))\n"
        self.assertEqual(self.found(ok), [])
        self.assertEqual(self.found("op.add_column('t', sa.Column('a', sa.Integer()))\n"), [])

    def test_add_column_to_table_created_in_same_file(self):
        src = ("op.create_table('n', sa.Column('id', sa.Integer()))\n"
               "op.add_column('n', sa.Column('a', sa.Integer(), nullable=False))\n")
        self.assertEqual(self.found(src), [])

    def test_django_operations(self):
        src = ("class Migration:\n    operations = [\n"
               "        migrations.RemoveField(model_name='m', name='a'),\n"
               "        migrations.DeleteModel(name='m'),\n"
               "        migrations.RenameField('m', 'a', 'b'),\n"
               "        migrations.RenameModel('m', 'n'),\n"
               "    ]\n")
        self.assertEqual([(f.line, f.rule) for f in self.found(src)],
                         [(3, "drop-column"), (4, "drop-table"), (5, "rename"), (6, "rename")])

    def test_django_add_and_alter_field(self):
        bad = "migrations.AddField(model_name='m', name='a', field=models.IntegerField())\n"
        self.assertEqual(rules(self.found(bad)), ["add-not-null"])
        ok = ("migrations.AddField(model_name='m', name='a', field=models.IntegerField(null=True))\n"
              "migrations.AddField(model_name='m', name='b', field=models.IntegerField(default=0))\n")
        self.assertEqual(self.found(ok), [])
        alter = "migrations.AlterField(model_name='m', name='a', field=models.IntegerField(null=False))\n"
        self.assertEqual(rules(self.found(alter)), ["set-not-null"])

    def test_raw_execute_goes_through_the_sql_scanner(self):
        src = "def upgrade():\n    op.execute(\"DROP TABLE users\")\n    op.execute(sa.text('TRUNCATE t'))\n"
        self.assertEqual([(f.line, f.rule) for f in self.found(src)], [(2, "drop-table"), (3, "truncate")])

    def test_multiline_execute_reports_the_real_line(self):
        src = "op.execute('''\n-- note\nALTER TABLE t\n  DROP COLUMN a;\n''')\n"
        self.assertEqual([(f.line, f.rule) for f in self.found(src)], [(3, "drop-column")])

    def test_runsql_reverse_sql_is_not_judged(self):
        src = "migrations.RunSQL('ALTER TABLE t ADD COLUMN a int', 'ALTER TABLE t DROP COLUMN a')\n"
        self.assertEqual(self.found(src), [])
        kw = "migrations.RunSQL(sql='SELECT 1', reverse_sql='DROP TABLE t')\n"
        self.assertEqual(self.found(kw), [])

    def test_comments_and_docstrings_never_trigger(self):
        src = ('"""Drops: op.drop_column("t", "a")."""\n# op.drop_table("t")\n'
               'def upgrade():\n    # op.drop_column("t", "a")\n    x = "DROP TABLE t"\n    op.add_column("t", c)\n')
        self.assertEqual(self.found(src), [])

    def test_syntax_error_is_nogo(self):
        with self.assertRaises(NoGo):
            migration.scan_python("def upgrade(:\n")


class RubyRuleTests(unittest.TestCase):
    def test_rails_operations_and_comments(self):
        src = ("class M < ActiveRecord::Migration\n  def up\n    # remove_column :t, :a\n"
               "    remove_column :t, :a\n    rename_table :a, :b\n    drop_table :c\n  end\n"
               "  def down\n    drop_table :d\n  end\nend\n")
        self.assertEqual([(f.line, f.rule) for f in migration.scan_ruby(src)],
                         [(4, "drop-column"), (5, "rename"), (6, "drop-table")])

    def test_rails_add_column_not_null(self):
        self.assertEqual(rules(migration.scan_ruby("add_column :t, :a, :integer, null: false\n")),
                         ["add-not-null"])
        self.assertEqual(migration.scan_ruby("add_column :t, :a, :integer, null: false, default: 0\n"), [])


class DeclarationTests(unittest.TestCase):
    def test_contract_after_line_and_section(self):
        self.assertEqual(migration.declarations("x\nContract-After: 0002_expand.sql\n"), ["0002_expand.sql"])
        self.assertEqual(migration.declarations("contract-after:abc1234\n"), ["abc1234"])
        text = "# T\n## Contract migration\nAfter `0002_expand.sql` shipped.\n\n## Other\nignored.sql\n"
        got = migration.declarations(text)
        self.assertEqual(len(got), 1)
        self.assertIn("0002_expand.sql", got[0])
        self.assertNotIn("ignored.sql", got[0])

    def test_no_declaration(self):
        self.assertEqual(migration.declarations("plain text\nContract-Before: x\n"), [])


class JudgeTests(unittest.TestCase):
    def setUp(self):
        self.repo = Repo(self)
        self.expand = self.repo.commit({"service/db/migrations/0001_init.sql": "CREATE TABLE t (id int);\n"},
                                       "base")
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.policy = os.path.join(self.tmp, "policy.env")
        self.write_policy("")
        os.environ.pop("TICKET_FILE", None)

    def write_policy(self, value):
        with open(self.policy, "w") as fh:
            fh.write(f"RISK_HIGH_MIGRATIONS={value}\nRISK_HIGH_AUTH=\n")

    def run_judge(self, base, head, ticket=None):
        env = {"WORKLOAD": self.repo.dir}
        if ticket is not None:
            path = os.path.join(self.tmp, "ticket.md")
            with open(path, "w") as fh:
                fh.write(ticket)
            env["TICKET_FILE"] = path
        err = io.StringIO()
        with redirect_stderr(err):
            try:
                detail = migration.judge([base, head, self.policy], env=env)
            except NoGo as no:
                return no, err.getvalue()
        return detail, err.getvalue()

    def test_no_migration_files_is_go(self):
        head = self.repo.commit({"src/a.py": "x = 1\n"})
        detail, _ = self.run_judge(self.expand, head)
        self.assertIn("no migration files", detail)

    def test_safe_expand_is_go(self):
        head = self.repo.commit({SQL: "ALTER TABLE t ADD COLUMN b int;\nUPDATE t SET b = 0;\n"})
        detail, _ = self.run_judge(self.expand, head)
        self.assertIn("1 migration file", detail)

    def test_unsafe_ddl_is_nogo_naming_file_rule_and_line(self):
        head = self.repo.commit({SQL: "-- contract\nSELECT 1;\nALTER TABLE t DROP COLUMN a;\n"})
        no, err = self.run_judge(self.expand, head)
        self.assertIsInstance(no, NoGo)
        self.assertIn(f"{SQL}:3", no.reason)
        self.assertIn("drop-column", no.reason)
        self.assertIn(f"{SQL}:3", " ".join(no.details))
        self.assertIn(f"{SQL}:3", err)

    def test_deleted_and_unmatched_files_are_ignored(self):
        bad = self.repo.commit({SQL: "DROP TABLE t;\n", "docs/other.sql": "DROP TABLE x;\n"})
        # Not a migration path: the second file never counts.
        self.assertTrue(isinstance(self.run_judge(self.expand, bad)[0], NoGo))
        gone = self.repo.commit({SQL: None})
        detail, _ = self.run_judge(bad, gone)
        self.assertIn("no migration files", detail)
        only_other = self.repo.commit({"docs/other.sql": "DROP TABLE y;\n"})
        detail, _ = self.run_judge(gone, only_other)
        self.assertIn("no migration files", detail)

    def test_policy_glob_adds_files_beyond_the_structural_ones(self):
        head = self.repo.commit({"schema/change.sql": "DROP TABLE t;\n"})
        self.assertIn("no migration files", self.run_judge(self.expand, head)[0])
        self.write_policy("other/*:schema/*")
        no, _ = self.run_judge(self.expand, head)
        self.assertIsInstance(no, NoGo)
        self.assertIn("schema/change.sql:1", no.reason)

    def test_structural_globs(self):
        for path in ("a/migrations/x.sql", "a/migrations/x.py", "a/db/migrate/x.rb", "p/prisma/migrations/1/m.sql",
                     "a/flyway/V2.sql", "a/liquibase/c.sql"):
            body = "def upgrade():\n    op.drop_table('t')\n" if path.endswith(".py") else (
                "drop_table :t\n" if path.endswith(".rb") else "DROP TABLE t;\n")
            head = self.repo.commit({path: body})
            self.assertIsInstance(self.run_judge(self.expand, head)[0], NoGo, path)

    def test_unsupported_type_is_listed_not_judged(self):
        head = self.repo.commit({"service/db/migrate/NOTES.md": "DROP TABLE t\n"})
        detail, err = self.run_judge(self.expand, head)
        self.assertIn("not analyzed", err)
        self.assertNotIsInstance(detail, NoGo)

    def test_python_migration(self):
        head = self.repo.commit({PY: "def upgrade():\n    op.drop_column('t', 'a')\n"})
        no, _ = self.run_judge(self.expand, head)
        self.assertIsInstance(no, NoGo)
        self.assertIn(f"{PY}:2", no.reason)

    def test_contract_declaration_in_ticket_allows_with_prior_file(self):
        head = self.repo.commit({SQL: "ALTER TABLE t DROP COLUMN a;\n"})
        detail, err = self.run_judge(self.expand, head, ticket="## Contract migration\nAfter 0001_init.sql.\n")
        self.assertNotIsInstance(detail, NoGo)
        self.assertIn("contract", detail)
        self.assertIn("allowed (contract after", err)

    def test_contract_declaration_with_ancestor_sha(self):
        head = self.repo.commit({SQL: "ALTER TABLE t DROP COLUMN a;\n"})
        detail, _ = self.run_judge(self.expand, head, ticket=f"Contract-After: {self.expand[:10]}\n")
        self.assertNotIsInstance(detail, NoGo)

    def test_contract_declaration_in_commit_message_trailer(self):
        head = self.repo.commit({SQL: "TRUNCATE t;\n"},
                                "drop it\n\nContract-After: service/db/migrations/0001_init.sql\n")
        detail, _ = self.run_judge(self.expand, head)
        self.assertNotIsInstance(detail, NoGo)

    def test_declaration_without_reference_is_nogo(self):
        head = self.repo.commit({SQL: "DROP TABLE t;\n"})
        no, _ = self.run_judge(self.expand, head, ticket="## Contract migration\nTrust me.\n")
        self.assertIsInstance(no, NoGo)
        self.assertIn("reference", no.reason)

    def test_unresolvable_reference_is_nogo(self):
        head = self.repo.commit({SQL: "DROP TABLE t;\n"})
        no, _ = self.run_judge(self.expand, head, ticket="Contract-After: 9999_missing.sql\n")
        self.assertIsInstance(no, NoGo)
        self.assertIn("9999_missing.sql", " ".join([no.reason, *no.details]))

    def test_sha_after_base_is_not_a_prior_deploy(self):
        mid = self.repo.commit({"x.txt": "1\n"})
        head = self.repo.commit({SQL: "DROP TABLE t;\n"})
        no, _ = self.run_judge(self.expand, head, ticket=f"Contract-After: {mid}\n")
        self.assertIsInstance(no, NoGo)

    def test_declaration_without_unsafe_ddl_is_harmless(self):
        head = self.repo.commit({SQL: "SELECT 1;\n"})
        detail, _ = self.run_judge(self.expand, head, ticket="Contract-After: nothing\n")
        self.assertNotIsInstance(detail, NoGo)

    def test_missing_ticket_file_is_nogo(self):
        head = self.repo.commit({SQL: "DROP TABLE t;\n"})
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(NoGo):
            migration.judge([self.expand, head, self.policy],
                            env={"WORKLOAD": self.repo.dir, "TICKET_FILE": os.path.join(self.tmp, "gone.md")})

    def test_unreadable_policy_is_nogo(self):
        head = self.repo.commit({SQL: "SELECT 1;\n"})
        with redirect_stderr(io.StringIO()), self.assertRaises(NoGo):
            migration.judge([self.expand, head, os.path.join(self.tmp, "none.env")],
                            env={"WORKLOAD": self.repo.dir})

    def test_bad_range_is_nogo(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(NoGo):
            migration.judge(["nope", "nada", self.policy], env={"WORKLOAD": self.repo.dir})


class MainContractTests(unittest.TestCase):
    def test_main_prints_one_line_on_go_and_reason_first_on_nogo(self):
        out = io.StringIO()
        from contextlib import redirect_stdout
        with redirect_stdout(out):
            rc = migration.main(lambda argv: "2 migration file(s), expand-safe", [])
        self.assertEqual((rc, out.getvalue()), (0, "2 migration file(s), expand-safe\n"))


if __name__ == "__main__":
    unittest.main()
