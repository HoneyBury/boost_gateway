#include "hello/hello.h"

#include <fmt/core.h>

#include <string_view>

namespace hello {

std::string make_message(std::string_view name) {
    return fmt::format("Hello, {}!", name);
}

}  // namespace hello
