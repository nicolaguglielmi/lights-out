pub fn add(a: i32, b: i32) -> i32 {
    a + b
}

pub fn is_healthy(status: u16) -> bool {
    status >= 200 && status < 400
}
