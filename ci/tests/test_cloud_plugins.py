"""Specification tests for M44 cloud delivery plugins & nested outside records (ADR 0104).

Tests all four cloud delivery plugins under plugins/cloud/ using stateful fake
CLIs injected via PATH (never calling live cloud APIs), plus shellcheck
cleanliness, slo.promql selectors against verify/slo.sh, `./go cloud-setup`
scaffolding, and core.gates.delivery write_record() for nested `outside/` paths.
"""

from __future__ import annotations

import contextlib
import http.server
import io
import json
import os
import shutil
import stat
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from core.gates import delivery
from core.gates.readiness import adapter_hash

ROOT = Path(__file__).resolve().parents[2]
PLUGINS_DIR = ROOT / "plugins" / "cloud"
PROVIDERS = (
    "gcp-cloud-deploy",
    "aws-ecs-codedeploy",
    "azure-container-apps",
    "hetzner-k3s",
)
HONESTY_PREFIX = "Contract-tested locally against stub CLIs; live cloud conformance requires your own cloud account via `./go delivery-conformance DELIVERY=outside/"
EXECUTABLE_VERBS = (
    "env.sh",
    "ready.sh",
    "deploy.sh",
    "shift.sh",
    "rollback.sh",
    "status.sh",
    "canary-url.sh",
)
REQUIRED_FILES = EXECUTABLE_VERBS + (
    "slo.promql",
    "README.md",
    "infra/main.tf",
    "infra/variables.tf",
    "infra/outputs.tf",
    "ci/github-oidc.yml",
    "ci/gitlab-oidc.yml",
)


def _write_exe(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IRUSR)


def _start_http_server(routes: dict[str, tuple[int, dict | str]]):
    requests: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def do_GET(self):  # noqa: N802
            path = self.path.split("?", 1)[0]
            requests.append(self.path)
            if path in routes:
                code, payload = routes[path]
                body = payload if isinstance(payload, bytes) else (
                    json.dumps(payload).encode() if isinstance(payload, dict) else str(payload).encode()
                )
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.end_headers()

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return srv, f"http://127.0.0.1:{srv.server_port}", requests


def _make_gcp_stubs(bin_dir: Path, state_file: Path, log_file: Path, app_url: str) -> None:
    state_file.write_text(json.dumps({
        "stable_rev": "backend-00001",
        "stable_ver": "0.1.0",
        "canary_rev": None,
        "canary_ver": None,
        "weight": 0,
        "release": None,
        "rollout": None,
        "ready": True,
        "canary_url": "https://canary---backend-stub.a.run.app",
        "app_url": app_url,
    }), encoding="utf-8")
    _write_exe(bin_dir / "gcloud", f"""#!/usr/bin/env python3
import json, sys
from pathlib import Path

state_p = Path({str(state_file)!r})
log_p = Path({str(log_file)!r})
args = sys.argv[1:]
with log_p.open("a", encoding="utf-8") as fh:
    fh.write("gcloud " + " ".join(args) + "\\n")
st = json.loads(state_p.read_text(encoding="utf-8"))

def opt(prefix):
    for i, a in enumerate(args):
        if a.startswith(prefix + "="):
            return a.split("=", 1)[1]
        if a == prefix and i + 1 < len(args):
            return args[i + 1]
    return ""

cmd = args[:3]
if cmd == ["deploy", "targets", "describe"]:
    print(json.dumps({{"targetId": args[3], "run": {{"location": "projects/p/locations/r"}}}}))
elif cmd == ["deploy", "targets", "rollback"]:
    st["weight"] = 0
    st["canary_rev"] = None
    st["canary_ver"] = None
    st["rollout"] = "rolled-back"
    state_p.write_text(json.dumps(st), encoding="utf-8")
    print(json.dumps({{"status": "SUCCEEDED"}}))
elif cmd == ["deploy", "releases", "create"]:
    rel = args[3]
    ann = opt("--annotations")
    ver = "0.2.0"
    if "lights-out.version=" in ann:
        ver = ann.split("lights-out.version=", 1)[1].split(",", 1)[0]
    st["release"] = rel
    st["canary_rev"] = "backend-00002"
    st["canary_ver"] = ver
    st["weight"] = 0
    st["rollout"] = rel + "-to-prod-0001"
    state_p.write_text(json.dumps(st), encoding="utf-8")
    print(json.dumps({{"name": rel}}))
elif cmd == ["deploy", "releases", "list"]:
    out = [{{"name": "projects/p/locations/r/deliveryPipelines/pipe/releases/" + st["release"]}}] if st["release"] else []
    print(json.dumps(out))
elif cmd == ["deploy", "rollouts", "list"]:
    out = [{{"name": "projects/p/locations/r/deliveryPipelines/pipe/releases/" + st["release"] + "/rollouts/" + st["rollout"], "state": "PENDING_RELEASE"}}] if st["rollout"] else []
    print(json.dumps(out))
elif cmd == ["deploy", "rollouts", "advance"]:
    phase = opt("--phase-id")
    if phase == "canary-5":
        st["weight"] = 5
    elif phase == "canary-25":
        st["weight"] = 25
    elif phase == "stable":
        st["weight"] = 100
    else:
        sys.stderr.write("unknown phase " + phase + "\\n")
        sys.exit(1)
    state_p.write_text(json.dumps(st), encoding="utf-8")
    print(json.dumps({{"phaseId": phase}}))
elif cmd == ["run", "services", "describe"]:
    traffic = [{{"revisionName": st["stable_rev"], "percent": 100 - st["weight"], "latestRevision": False}}]
    if st["canary_rev"]:
        traffic.append({{"revisionName": st["canary_rev"], "percent": st["weight"], "tag": "canary", "url": st["canary_url"]}})
    print(json.dumps({{
        "status": {{
            "url": st["app_url"],
            "conditions": [{{"type": "Ready", "status": "True" if st["ready"] else "False"}}],
            "traffic": traffic,
        }}
    }}))
elif cmd == ["run", "revisions", "describe"]:
    rev = args[3]
    ver = st["canary_ver"] if rev == st["canary_rev"] else st["stable_ver"]
    print(json.dumps({{
        "metadata": {{"name": rev, "labels": {{"lights-out.version": ver}}}},
        "spec": {{"containers": [{{"env": [{{"name": "APP_VERSION", "value": ver}}]}}]}},
    }}))
else:
    sys.stderr.write("unexpected gcloud cmd: " + " ".join(args) + "\\n")
    sys.exit(1)
""")


def _make_aws_stubs(bin_dir: Path, state_file: Path, log_file: Path, app_url: str) -> None:
    state_file.write_text(json.dumps({
        "stable_td": "arn:aws:ecs:eu-west-1:000000000000:task-definition/backend:1",
        "stable_ver": "0.1.0",
        "canary_td": None,
        "canary_ver": None,
        "weight": 0,
        "deployment_id": None,
        "canary_url": "https://canary-test.elb.example.invalid:8443",
        "app_url": app_url,
    }), encoding="utf-8")
    _write_exe(bin_dir / "aws", f"""#!/usr/bin/env python3
import json, sys
from pathlib import Path

state_p = Path({str(state_file)!r})
log_p = Path({str(log_file)!r})
args = sys.argv[1:]
with log_p.open("a", encoding="utf-8") as fh:
    fh.write("aws " + " ".join(args) + "\\n")
st = json.loads(state_p.read_text(encoding="utf-8"))

def opt(flag):
    for i, a in enumerate(args):
        if a == flag and i + 1 < len(args):
            return args[i + 1]
    return ""

# Strip global options like --region X --output json
clean = []
skip = False
for a in args:
    if skip:
        skip = False
        continue
    if a in ("--region", "--output"):
        skip = True
        continue
    clean.append(a)

svc, action = clean[0], clean[1]
if svc == "ecs" and action == "describe-services":
    task_sets = [{{"id": "ecs-svc/stable", "status": "PRIMARY", "taskDefinition": st["stable_td"], "runningCount": 1}}]
    if st["canary_td"]:
        task_sets.append({{"id": "ecs-svc/canary", "status": "ACTIVE", "taskDefinition": st["canary_td"], "runningCount": 1}})
    print(json.dumps({{"services": [{{"status": "ACTIVE", "runningCount": 1, "desiredCount": 1, "taskDefinition": st["stable_td"], "taskSets": task_sets}}]}}))
elif svc == "ecs" and action == "describe-task-definition":
    td_name = opt("--task-definition")
    ver = st["canary_ver"] if td_name == st["canary_td"] else st["stable_ver"]
    print(json.dumps({{"taskDefinition": {{
        "family": "backend",
        "taskDefinitionArn": td_name,
        "containerDefinitions": [{{"name": "backend", "image": "img", "environment": [{{"name": "APP_VERSION", "value": ver}}], "dockerLabels": {{"lights-out.version": ver}}}}],
    }}}}))
elif svc == "ecs" and action == "register-task-definition":
    raw = opt("--cli-input-json")
    data = json.loads(raw)
    cdef = data["containerDefinitions"][0]
    ver = cdef.get("dockerLabels", {{}}).get("lights-out.version") or next((e["value"] for e in cdef.get("environment", []) if e["name"] == "APP_VERSION"), "0.2.0")
    st["canary_td"] = "arn:aws:ecs:eu-west-1:000000000000:task-definition/backend:2"
    st["canary_ver"] = ver
    state_p.write_text(json.dumps(st), encoding="utf-8")
    print(json.dumps({{"taskDefinition": {{"taskDefinitionArn": st["canary_td"]}}}}))
elif svc == "deploy" and action == "get-deployment-group":
    print(json.dumps({{"deploymentGroupInfo": {{
        "deploymentGroupName": "backend-dg",
        "loadBalancerInfo": {{"targetGroupPairInfoList": [{{
            "targetGroups": [{{"name": "tg-blue"}}, {{"name": "tg-green"}}],
            "prodTrafficRoute": {{"listenerArns": ["arn:aws:elasticloadbalancing:eu-west-1:000000000000:listener/app/lb/1/p"]}},
            "testTrafficRoute": {{"listenerArns": ["arn:aws:elasticloadbalancing:eu-west-1:000000000000:listener/app/lb/1/t"]}},
        }}]}},
    }}}}))
elif svc == "deploy" and action == "create-deployment":
    st["deployment_id"] = "d-0001"
    st["weight"] = 0
    state_p.write_text(json.dumps(st), encoding="utf-8")
    print(json.dumps({{"deploymentId": "d-0001"}}))
elif svc == "deploy" and action == "get-deployment":
    print(json.dumps({{"deploymentInfo": {{"deploymentId": "d-0001", "status": "Ready"}}}}))
elif svc == "deploy" and action == "list-deployments":
    print(json.dumps({{"deployments": [st["deployment_id"]] if st["deployment_id"] else []}}))
elif svc == "deploy" and action == "continue-deployment":
    print(json.dumps({{}}))
elif svc == "deploy" and action == "stop-deployment":
    st["deployment_id"] = None
    st["canary_td"] = None
    st["canary_ver"] = None
    st["weight"] = 0
    state_p.write_text(json.dumps(st), encoding="utf-8")
    print(json.dumps({{"status": "Succeeded"}}))
elif svc == "elbv2" and action == "describe-target-groups":
    print(json.dumps({{"TargetGroups": [
        {{"TargetGroupName": "tg-blue", "TargetGroupArn": "arn:tg:blue"}},
        {{"TargetGroupName": "tg-green", "TargetGroupArn": "arn:tg:green"}},
    ]}}))
elif svc == "elbv2" and action == "describe-rules":
    print(json.dumps({{"Rules": [{{
        "RuleArn": "arn:rule:1",
        "IsDefault": True,
        "Actions": [{{"Type": "forward", "ForwardConfig": {{"TargetGroups": [
            {{"TargetGroupArn": "arn:tg:blue", "Weight": 100 - st["weight"]}},
            {{"TargetGroupArn": "arn:tg:green", "Weight": st["weight"]}},
        ]}}}}],
    }}]}}))
elif svc == "elbv2" and action == "modify-rule":
    actions = json.loads(opt("--actions"))
    tgs = actions[0]["ForwardConfig"]["TargetGroups"]
    canary_w = next(t["Weight"] for t in tgs if t["TargetGroupArn"] == "arn:tg:green")
    st["weight"] = int(canary_w)
    state_p.write_text(json.dumps(st), encoding="utf-8")
    print(json.dumps({{"Rules": []}}))
elif svc == "elbv2" and action == "describe-listeners":
    print(json.dumps({{"Listeners": [{{"ListenerArn": opt("--listener-arns"), "Port": 8443, "Protocol": "HTTPS", "LoadBalancerArn": "arn:lb:1"}}]}}))
elif svc == "elbv2" and action == "describe-load-balancers":
    print(json.dumps({{"LoadBalancers": [{{"LoadBalancerArn": "arn:lb:1", "DNSName": "canary-test.elb.example.invalid"}}]}}))
else:
    sys.stderr.write("unexpected aws cmd: " + " ".join(args) + "\\n")
    sys.exit(1)
""")


def _make_az_stubs(bin_dir: Path, state_file: Path, log_file: Path, app_url: str) -> None:
    state_file.write_text(json.dumps({
        "stable_rev": "app--stable",
        "stable_ver": "0.1.0",
        "canary_rev": None,
        "canary_ver": None,
        "weight": 0,
        "fqdn": app_url.replace("http://", ""),
        "app_url": app_url,
    }), encoding="utf-8")
    _write_exe(bin_dir / "az", f"""#!/usr/bin/env python3
import json, sys
from pathlib import Path

state_p = Path({str(state_file)!r})
log_p = Path({str(log_file)!r})
args = sys.argv[1:]
with log_p.open("a", encoding="utf-8") as fh:
    fh.write("az " + " ".join(args) + "\\n")
st = json.loads(state_p.read_text(encoding="utf-8"))

def opt(flag):
    for i, a in enumerate(args):
        if a == flag and i + 1 < len(args):
            return args[i + 1]
    return ""

cmd = args[:3]
if cmd[:2] == ["containerapp", "show"]:
    traffic = [{{"revisionName": st["stable_rev"], "label": "stable", "weight": 100 - st["weight"]}}]
    if st["canary_rev"]:
        traffic.append({{"revisionName": st["canary_rev"], "label": "canary", "weight": st["weight"]}})
    print(json.dumps({{
        "properties": {{
            "provisioningState": "Succeeded",
            "runningStatus": "Running",
            "configuration": {{
                "ingress": {{"fqdn": st["fqdn"], "traffic": traffic}},
            }},
        }}
    }}))
elif cmd == ["containerapp", "revision", "set-mode"]:
    print(json.dumps({{"mode": "Multiple"}}))
elif cmd[:2] == ["containerapp", "update"]:
    suf = opt("--revision-suffix")
    env_arg = opt("--set-env-vars")
    ver = env_arg.split("APP_VERSION=", 1)[1].split(" ", 1)[0] if "APP_VERSION=" in env_arg else "0.2.0"
    rev = "app--" + (suf or "canary")
    st["canary_rev"] = rev
    st["canary_ver"] = ver
    st["weight"] = 0
    state_p.write_text(json.dumps(st), encoding="utf-8")
    print(json.dumps({{"properties": {{"latestRevisionName": rev}}}}))
elif cmd == ["containerapp", "revision", "label"]:
    print(json.dumps({{"label": opt("--label")}}))
elif cmd == ["containerapp", "revision", "list"]:
    revs = [{{
        "name": st["stable_rev"],
        "properties": {{
            "active": True,
            "runningState": "Running",
            "trafficWeight": 100 - st["weight"],
            "template": {{"containers": [{{"image": "reg/app:0.1.0", "env": [{{"name": "APP_VERSION", "value": st["stable_ver"]}}]}}]}},
        }},
    }}]
    if st["canary_rev"]:
        revs.append({{
            "name": st["canary_rev"],
            "properties": {{
                "active": True,
                "runningState": "Running",
                "trafficWeight": st["weight"],
                "template": {{"containers": [{{"image": "reg/app:0.2.0", "env": [{{"name": "APP_VERSION", "value": st["canary_ver"]}}]}}]}},
            }},
        }})
    print(json.dumps(revs))
elif cmd == ["containerapp", "revision", "show"]:
    print(json.dumps({{"name": opt("--revision"), "properties": {{"active": True, "runningState": "Running"}}}}))
elif cmd == ["containerapp", "revision", "deactivate"]:
    st["canary_rev"] = None
    st["canary_ver"] = None
    st["weight"] = 0
    state_p.write_text(json.dumps(st), encoding="utf-8")
    print(json.dumps({{}}))
elif args[:4] == ["containerapp", "ingress", "traffic", "set"]:
    for a in args:
        if a.startswith("canary="):
            st["weight"] = int(a.split("=", 1)[1])
    state_p.write_text(json.dumps(st), encoding="utf-8")
    print(json.dumps({{"traffic": []}}))
else:
    sys.stderr.write("unexpected az cmd: " + " ".join(args) + "\\n")
    sys.exit(1)
""")


def _make_hetzner_stubs(bin_dir: Path, state_file: Path, log_file: Path, app_url: str) -> None:
    state_file.write_text(json.dumps({
        "stable_rs": "rs-stable",
        "stable_ver": "0.1.0",
        "canary_rs": "rs-stable",
        "canary_ver": None,
        "weight": 0,
        "step_index": 0,
        "paused": False,
        "abort": False,
        "phase": "Healthy",
        "canary_url": "https://canary.hetzner.example.invalid",
        "app_url": app_url,
    }), encoding="utf-8")
    steps = [
        {"setWeight": 5},
        {"pause": {}},
        {"setWeight": 25},
        {"pause": {}},
        {"setWeight": 100},
        {"pause": {}},
    ]
    _write_exe(bin_dir / "kubectl", f"""#!/usr/bin/env python3
import json, sys
from pathlib import Path

state_p = Path({str(state_file)!r})
log_p = Path({str(log_file)!r})
args = sys.argv[1:]
with log_p.open("a", encoding="utf-8") as fh:
    fh.write("kubectl " + " ".join(args) + "\\n")
st = json.loads(state_p.read_text(encoding="utf-8"))
steps = {json.dumps(steps)}

clean = []
i = 0
while i < len(args):
    if args[i] in ("--kubeconfig", "--namespace", "-n", "-l", "--type", "-p", "-o"):
        i += 2
        continue
    if args[i].startswith(("--kubeconfig=", "--namespace=", "--subresource=", "--type=", "-o=")):
        i += 1
        continue
    clean.append(args[i])
    i += 1

def opt(flag):
    for j, a in enumerate(args):
        if a == flag and j + 1 < len(args):
            return args[j + 1]
    return ""

action = clean[0]
if action == "get" and clean[1] == "rollout":
    pauses = [{{"reason": "CanaryPauseStep"}}] if st["paused"] else []
    print(json.dumps({{
        "spec": {{"template": {{"metadata": {{"annotations": {{}}}}}}, "strategy": {{"canary": {{"steps": steps}}}}}},
        "status": {{
            "phase": st["phase"],
            "stableRS": st["stable_rs"],
            "currentPodHash": st["canary_rs"],
            "currentStepIndex": st["step_index"],
            "abort": st["abort"],
            "pauseConditions": pauses,
            "canary": {{"weights": {{"canary": {{"weight": st["weight"]}}}}}},
        }},
    }}))
elif action == "get" and clean[1] == "pods":
    lbl = opt("-l")
    rs = lbl.split("=", 1)[1] if "=" in lbl else st["stable_rs"]
    pod_name = "pod-" + rs
    print(json.dumps({{"items": [{{"metadata": {{"name": pod_name}}, "status": {{"conditions": [{{"type": "Ready", "status": "True"}}]}}}}]}}))
elif action == "get" and "--raw" in args:
    raw_path = args[args.index("--raw") + 1]
    ver = st["canary_ver"] if st["canary_rs"] in raw_path and st["canary_ver"] else st["stable_ver"]
    print(json.dumps({{"version": ver}}))
elif action == "get" and clean[1] == "ingress":
    print(json.dumps({{"spec": {{"rules": [{{"host": "canary.hetzner.example.invalid"}}]}}, "status": {{"loadBalancer": {{"ingress": [{{"ip": "203.0.113.10"}}]}}}}}}))
elif action == "patch" and clean[1] == "rollout":
    patch = json.loads(opt("-p"))
    if isinstance(patch, list):
        ann = next((op["value"] for op in patch if op.get("path") == "/spec/template/metadata/annotations"), {{}})
        st["canary_ver"] = ann.get("lights-out.dev/version", "0.2.0")
        st["canary_rs"] = "rs-canary"
        st["weight"] = 0
        st["step_index"] = 0
        st["paused"] = True
        st["abort"] = False
        st["phase"] = "Paused"
    elif "status" in patch:
        if patch["status"].get("abort") is True:
            st["abort"] = True
            st["weight"] = 0
            st["paused"] = False
            st["phase"] = "Degraded"
        elif patch["status"].get("abort") is False:
            st["abort"] = False
        elif patch["status"].get("pauseConditions") is None:
            # Advance to next pause after the weight step
            idx = st["step_index"]
            if idx < len(steps) and "setWeight" in steps[idx]:
                st["weight"] = steps[idx]["setWeight"]
                st["step_index"] = idx + 1
            elif idx + 1 < len(steps) and "setWeight" in steps[idx + 1]:
                st["weight"] = steps[idx + 1]["setWeight"]
                st["step_index"] = idx + 2
            st["paused"] = True
            st["phase"] = "Paused"
    state_p.write_text(json.dumps(st), encoding="utf-8")
    print(json.dumps({{"kind": "Rollout"}}))
else:
    sys.stderr.write("unexpected kubectl cmd: " + " ".join(args) + "\\n")
    sys.exit(1)
""")
    _write_exe(bin_dir / "hcloud", f"""#!/usr/bin/env python3
import json, sys
from pathlib import Path
Path({str(log_file)!r}).open("a", encoding="utf-8").write("hcloud " + " ".join(sys.argv[1:]) + "\\n")
if sys.argv[1:3] == ["server", "list"]:
    print(json.dumps([{{"name": "k3s-cp-1", "status": "running"}}]))
elif sys.argv[1:3] == ["load-balancer", "describe"]:
    print(json.dumps({{"public_net": {{"ipv4": {{"ip": "203.0.113.10"}}}}}}))
else:
    print("[]")
""")


class CloudPluginsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.srv, self.app_url, self.http_reqs = _start_http_server({
            "/healthz": (200, {"status": "ok", "version": "0.1.0"}),
        })
        self.addCleanup(self.srv.shutdown)
        self.addCleanup(self.srv.server_close)

    def _base_env(self, provider: str, bin_dir: Path, workload: Path) -> dict[str, str]:
        env = {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "WORKLOAD": str(workload),
            "APP_URL": self.app_url,
            "READY_TIMEOUT": "2",
        }
        if provider == "gcp-cloud-deploy":
            env.update({
                "GCP_PROJECT": "demo-proj-01",
                "GCP_REGION": "europe-west1",
                "CLOUD_DEPLOY_PIPELINE": "backend-pipe",
                "CLOUD_DEPLOY_TARGET": "prod",
                "CLOUD_RUN_SERVICE": "backend",
            })
        elif provider == "aws-ecs-codedeploy":
            env.update({
                "AWS_REGION": "eu-west-1",
                "ECS_CLUSTER": "prod-ecs",
                "ECS_SERVICE": "backend",
                "CODEDEPLOY_APP": "backend-cd",
                "CODEDEPLOY_GROUP": "backend-dg",
            })
        elif provider == "azure-container-apps":
            env.update({
                "AZURE_SUBSCRIPTION_ID": "00000000-0000-0000-0000-000000000000",
                "AZURE_RESOURCE_GROUP": "rg-lights-out",
                "AZURE_CONTAINER_APP": "backend",
            })
        elif provider == "hetzner-k3s":
            kube = self.root / "ephemeral-kubeconfig.yaml"
            kube.write_text("apiVersion: v1\nkind: Config\n", encoding="utf-8")
            env.update({
                "HETZNER_CLUSTER": "prod-k3s",
                "HETZNER_KUBECONFIG": str(kube),
                "HETZNER_NAMESPACE": "lights-out",
                "HETZNER_ROLLOUT": "backend",
            })
        return env

    def _run_verb(self, provider: str, verb: str, args: list[str], env: dict[str, str]):
        script = PLUGINS_DIR / provider / verb
        return subprocess.run(
            ["sh", str(script), *args],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_required_files_and_honesty_notice(self):
        self.assertTrue((PLUGINS_DIR / "README.md").is_file(), "missing plugins/cloud/README.md")
        self.assertTrue((PLUGINS_DIR / "setup.py").is_file(), "missing plugins/cloud/setup.py")
        top_readme = (PLUGINS_DIR / "README.md").read_text(encoding="utf-8")
        self.assertIn(HONESTY_PREFIX, top_readme)
        for p in PROVIDERS:
            pdir = PLUGINS_DIR / p
            self.assertTrue(pdir.is_dir(), f"missing {pdir}")
            for rel in REQUIRED_FILES:
                f = pdir / rel
                self.assertTrue(f.is_file(), f"missing {f}")
            for sh_name in EXECUTABLE_VERBS:
                mode = (pdir / sh_name).stat().st_mode
                self.assertTrue(bool(mode & stat.S_IXUSR), f"{p}/{sh_name} is not executable")
            readme = (pdir / "README.md").read_text(encoding="utf-8")
            self.assertIn(HONESTY_PREFIX, readme, p)
            tf_text = (pdir / "infra" / "main.tf").read_text(encoding="utf-8")
            self.assertRegex(tf_text.lower(), r"oidc|workload_identity|federated")
            self.assertRegex((pdir / "infra" / "variables.tf").read_text(encoding="utf-8").lower(), r"budget")

    def test_shellcheck_sh_cleanliness(self):
        shellcheck = shutil.which("shellcheck")
        self.assertIsNotNone(shellcheck, "shellcheck must be available on PATH inside workbench")
        scripts = [str(PLUGINS_DIR / p / v) for p in PROVIDERS for v in EXECUTABLE_VERBS]
        res = subprocess.run([shellcheck, "-s", "sh", "--color=never", *scripts], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def test_env_sh_missing_var_and_static_credential_refusal(self):
        required_keys = {
            "gcp-cloud-deploy": ("GCP_PROJECT", "GCP_REGION", "CLOUD_DEPLOY_PIPELINE"),
            "aws-ecs-codedeploy": ("AWS_REGION", "ECS_CLUSTER", "ECS_SERVICE", "CODEDEPLOY_APP", "CODEDEPLOY_GROUP"),
            "azure-container-apps": ("AZURE_SUBSCRIPTION_ID", "AZURE_RESOURCE_GROUP", "AZURE_CONTAINER_APP"),
            "hetzner-k3s": ("HETZNER_CLUSTER", "HETZNER_KUBECONFIG"),
        }
        for p in PROVIDERS:
            wl = self.root / f"wl-{p}"
            wl.mkdir(parents=True, exist_ok=True)
            bin_dir = self.root / f"bin-env-{p}"
            bin_dir.mkdir(exist_ok=True)
            base = self._base_env(p, bin_dir, wl)

            # Missing required config variable must refuse
            for key in required_keys[p]:
                bad_env = dict(base)
                bad_env[key] = ""
                if key == "GCP_PROJECT":
                    bad_env["GOOGLE_CLOUD_PROJECT"] = ""
                elif key == "GCP_REGION":
                    bad_env["GOOGLE_CLOUD_LOCATION"] = ""
                elif key == "AWS_REGION":
                    bad_env["AWS_DEFAULT_REGION"] = ""
                elif key == "HETZNER_KUBECONFIG":
                    bad_env["KUBECONFIG"] = ""
                res = subprocess.run(
                    ["sh", "-c", f'. "{PLUGINS_DIR / p / "env.sh"}"'],
                    env=bad_env,
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(res.returncode, 0, f"{p} accepted empty {key}")
                self.assertTrue(res.stderr.strip(), f"{p} empty {key} printed nothing to stderr")

            # Static credential files in WORKLOAD must refuse
            cred_cases = [
                ("sa.json", '{"type": "service_account", "project_id": "demo"}\n'),
                (".aws/credentials", "[default]\naws_access_key_id = EXAMPLE0000\n"),
                ("azure-credentials.json", '{"clientId": "x", "clientSecret": "y"}\n'),
                ("deploy.pem", "stub-certificate-material\n"),
            ]
            for rel, body in cred_cases:
                wl_cred = self.root / f"wl-cred-{p}-{rel.replace('/', '_')}"
                target = wl_cred / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(body, encoding="utf-8")
                bad_env = dict(base, WORKLOAD=str(wl_cred))
                res = subprocess.run(
                    ["sh", "-c", f'. "{PLUGINS_DIR / p / "env.sh"}"'],
                    env=bad_env,
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(res.returncode, 0, f"{p} accepted static credential file {rel}")
                self.assertIn(rel.split("/")[-1], res.stderr, f"{p} refusal did not name {rel}")

    def test_delivery_verbs_contract_for_all_four_providers(self):
        stub_makers = {
            "gcp-cloud-deploy": _make_gcp_stubs,
            "aws-ecs-codedeploy": _make_aws_stubs,
            "azure-container-apps": _make_az_stubs,
            "hetzner-k3s": _make_hetzner_stubs,
        }
        for p in PROVIDERS:
            with self.subTest(provider=p):
                wl = self.root / f"wl-run-{p}"
                wl.mkdir(parents=True, exist_ok=True)
                bin_dir = self.root / f"bin-{p}"
                bin_dir.mkdir(exist_ok=True)
                state_file = self.root / f"state-{p}.json"
                log_file = self.root / f"log-{p}.txt"
                stub_makers[p](bin_dir, state_file, log_file, self.app_url)
                env = self._base_env(p, bin_dir, wl)

                # ready.sh
                r = self._run_verb(p, "ready.sh", [], env)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(r.stdout, "", f"{p} ready.sh must be silent on stdout")

                # deploy.sh <version> <image>
                r = self._run_verb(p, "deploy.sh", ["0.2.0-canary", "repo/app@sha256:" + "a" * 64], env)
                self.assertEqual(r.returncode, 0, r.stderr)

                # status.sh at weight 0
                r = self._run_verb(p, "status.sh", [], env)
                self.assertEqual(r.returncode, 0, r.stderr)
                st = json.loads(r.stdout)
                self.assertEqual(sorted(st.keys()), ["canary", "stable", "weight"])
                self.assertIsInstance(st["stable"], str)
                self.assertEqual(st["canary"], "0.2.0-canary")
                self.assertIs(type(st["weight"]), int)
                self.assertEqual(st["weight"], 0)

                # canary-url.sh
                r = self._run_verb(p, "canary-url.sh", [], env)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertRegex(r.stdout.strip(), r"^https?://\S+$")

                # shift.sh invalid percentages must refuse
                for bad_pct in ("0", "50", "101", "-5", "abc"):
                    r = self._run_verb(p, "shift.sh", [bad_pct], env)
                    self.assertNotEqual(r.returncode, 0, f"{p} shift.sh accepted {bad_pct}")
                    self.assertTrue(r.stderr.strip(), f"{p} shift.sh {bad_pct} printed no stderr reason")

                # shift.sh 5 -> 25 -> 100
                for pct in (5, 25, 100):
                    r = self._run_verb(p, "shift.sh", [str(pct)], env)
                    self.assertEqual(r.returncode, 0, f"{p} shift.sh {pct}: {r.stderr}")
                    st_r = self._run_verb(p, "status.sh", [], env)
                    self.assertEqual(st_r.returncode, 0, st_r.stderr)
                    st = json.loads(st_r.stdout)
                    self.assertEqual(sorted(st.keys()), ["canary", "stable", "weight"])
                    self.assertEqual(st["weight"], pct)

                # idempotent rollback.sh (twice)
                for attempt in (1, 2):
                    r = self._run_verb(p, "rollback.sh", [], env)
                    self.assertEqual(r.returncode, 0, f"{p} rollback #{attempt}: {r.stderr}")
                    st_r = self._run_verb(p, "status.sh", [], env)
                    self.assertEqual(st_r.returncode, 0, st_r.stderr)
                    st = json.loads(st_r.stdout)
                    self.assertEqual(sorted(st.keys()), ["canary", "stable", "weight"])
                    self.assertEqual(st["weight"], 0)
                    self.assertIsNone(st["canary"])

                self.assertTrue(log_file.read_text(encoding="utf-8").strip(), f"{p} never invoked its CLI")

    def test_slo_promql_selectors_and_verify_slo(self):
        responses = {
            "traffic": [
                {"metric": {"service_version": "v-canary"}, "value": [1, "1.0"]},
                {"metric": {"service_version": "v-stable"}, "value": [1, "4.0"]},
            ],
            "error_rate": [
                {"metric": {"service_version": "v-canary"}, "value": [1, "0.001"]},
                {"metric": {"service_version": "v-stable"}, "value": [1, "0.001"]},
            ],
            "latency_p95": [
                {"metric": {"service_version": "v-canary"}, "value": [1, "0.05"]},
                {"metric": {"service_version": "v-stable"}, "value": [1, "0.04"]},
            ],
        }

        class PromHandler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_GET(self):  # noqa: N802
                from urllib.parse import parse_qs, urlparse
                q = parse_qs(urlparse(self.path).query).get("query", [""])[0]
                if "5.." in q:
                    res = responses["error_rate"]
                elif "histogram_quantile" in q:
                    res = responses["latency_p95"]
                else:
                    res = responses["traffic"]
                body = json.dumps({"status": "success", "data": {"result": res}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        prom = http.server.ThreadingHTTPServer(("127.0.0.1", 0), PromHandler)
        t = threading.Thread(target=prom.serve_forever, daemon=True)
        t.start()
        self.addCleanup(prom.shutdown)
        self.addCleanup(prom.server_close)

        for p in PROVIDERS:
            promql = PLUGINS_DIR / p / "slo.promql"
            text = promql.read_text(encoding="utf-8")
            for name in ("traffic", "error_rate", "latency_p95"):
                self.assertIn(f"# name: {name}", text)
            self.assertIn("service_version", text)
            self.assertIn("target_info", text)
            self.assertIn('http_response_status_code=~"5.."', text)
            env = {
                **os.environ,
                "WORKLOAD_CONTRACT": str(ROOT / "app"),
                "SLO_PROMQL": str(promql),
                "CANARY_VERSION": "v-canary",
                "STABLE_VERSION": "v-stable",
                "METRICS_QUERY": "adapters/promql/query.sh",
                "METRICS_URL": f"http://127.0.0.1:{prom.server_port}",
            }
            res = subprocess.run(["sh", "verify/slo.sh"], cwd=ROOT, env=env, capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, f"{p} slo.promql failed verify/slo.sh: {res.stdout}\n{res.stderr}")
            self.assertEqual(res.stdout.strip(), "GO: slo")

    def test_cloud_setup_target_and_output_contract(self):
        go_text = (ROOT / "go").read_text(encoding="utf-8")
        self.assertIn("cloud-setup", go_text)
        makefile_text = (ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn("cloud-setup:", makefile_text)

        out_base = self.root / "crew-cloud"
        wl_dir = self.root / "user-workload"
        wl_dir.mkdir()
        (wl_dir / "README.md").write_text("untouched\n", encoding="utf-8")

        for p in PROVIDERS:
            res = subprocess.run(
                ["make", "--no-print-directory", "cloud-setup", f"CLOUD={p}",
                 f"CLOUD_SETUP_DIR={out_base}", f"WORKLOAD={wl_dir}"],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            lines = res.stdout.splitlines()
            self.assertEqual(len(lines), 1, res.stdout)
            self.assertTrue(lines[0].startswith("GO: cloud-setup — "), lines[0])
            self.assertIn("ADAPTERS_PATH", res.stderr)
            self.assertIn(f"DELIVERY=outside/{p}", res.stderr)
            self.assertIn("delivery-conformance", res.stderr)
            staged = out_base / p
            self.assertTrue((staged / "infra" / "main.tf").is_file())
            self.assertTrue((staged / "infra" / "variables.tf").is_file())
            self.assertTrue((staged / "infra" / "outputs.tf").is_file())
            self.assertTrue((staged / "ci" / "github-oidc.yml").is_file())
            self.assertTrue((staged / "ci" / "gitlab-oidc.yml").is_file())
            self.assertEqual([x.name for x in wl_dir.iterdir()], ["README.md"], "cloud-setup wrote into WORKLOAD")

        bad = subprocess.run(
            ["make", "--no-print-directory", "cloud-setup", "CLOUD=no-such-cloud", f"CLOUD_SETUP_DIR={out_base}"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(bad.returncode, 0)
        self.assertTrue(bad.stdout.strip().startswith("NO-GO: cloud-setup — "), bad.stdout)

    def test_delivery_write_record_supports_nested_outside_path(self):
        cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, cwd)
        adapter_dir = self.root / "adapters" / "outside" / "gcp-cloud-deploy"
        adapter_dir.mkdir(parents=True)
        (adapter_dir / "rollback.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

        class StubPlane:
            def run(self, argv, env=None, quiet=False):
                return 0, ""

            def healthz(self, url):
                return "0.1.0"

        env = {
            "DELIVERY": "outside/gcp-cloud-deploy",
            "FLAGS": "flagd",
            "WORKLOAD_KILL_SWITCH": "waitlist",
            "CANARY_VERSION": "0.2.0",
            "CANARY_TAG": "sha-" + "a" * 40,
            "CONFORMANCE_DIR": str(self.root / "conformance"),
        }
        suite = delivery.Suite(env, StubPlane())
        with contextlib.redirect_stderr(io.StringIO()):
            suite.write_record()
        record_path = self.root / "conformance" / "delivery-outside" / "gcp-cloud-deploy.env"
        self.assertTrue(record_path.is_file(), f"missing {record_path}")
        content = record_path.read_text(encoding="utf-8")
        self.assertIn("ADAPTER=outside/gcp-cloud-deploy\n", content)
        self.assertIn(f"HASH={adapter_hash(str(adapter_dir))}\n", content)


if __name__ == "__main__":
    unittest.main()
