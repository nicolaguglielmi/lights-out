package com.example;

public final class Calc {
    private Calc() {}

    public static int add(int a, int b) {
        return a + b;
    }

    public static boolean isHealthy(int status) {
        return status >= 200 && status < 400;
    }
}
