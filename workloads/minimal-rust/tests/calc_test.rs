use minimal_rust::{add, is_healthy};

#[test]
fn adds_numbers() {
    assert_eq!(add(2, 3), 5);
}

#[test]
fn checks_health_bounds() {
    assert!(is_healthy(200));
    assert!(!is_healthy(199));
    assert!(!is_healthy(400));
}
