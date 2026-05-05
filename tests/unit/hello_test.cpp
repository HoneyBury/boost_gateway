#include "app/logging.h"
#include "hello/hello.h"

#include <gtest/gtest.h>

TEST(HelloTest, FormatsGreeting) {
    EXPECT_EQ(hello::make_message("Codex"), "Hello, Codex!");
}

TEST(LoggingTest, InitDoesNotThrow) {
    EXPECT_NO_THROW(app::logging::init("hello_tests"));
}
