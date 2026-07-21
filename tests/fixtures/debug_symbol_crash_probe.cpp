#include <csignal>
#include <cstdlib>

__attribute__((noinline)) void controlled_crash_site() {
    std::raise(SIGSEGV);
}

int main() {
    controlled_crash_site();
    return EXIT_SUCCESS;
}
