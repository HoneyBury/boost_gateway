#include "app/logging.h"
#include "hello/hello.h"

int main() {
    app::logging::init("hello_world");

    LOG_INFO("Application started");
    LOG_DEBUG("Preparing greeting for the demo");
    LOG_INFO("{}", hello::make_message("World"));

    return 0;
}
