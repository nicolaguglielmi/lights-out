package com.example;

public class CalcTest {
    public static void main(String[] args) {
        if (Calc.add(2, 3) != 5) {
            throw new AssertionError("add failed");
        }
        if (!Calc.isHealthy(200) || Calc.isHealthy(400)) {
            throw new AssertionError("health check failed");
        }
    }
}
