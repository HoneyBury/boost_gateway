#pragma once

#include <atomic>
#include <memory>

namespace v2::config {

/// Thread-safe wrapper around an immutable config snapshot.
/// Uses std::shared_ptr<const T> for lock-free reads and
/// read-copy-update semantics.
template <typename T>
class SharedConfig {
public:
    SharedConfig() : ptr_(std::make_shared<const T>()) {}
    explicit SharedConfig(T config)
        : ptr_(std::make_shared<const T>(std::move(config))) {}

    /// Atomically read the current config snapshot.
    /// Callers hold a shared_ptr<const T> that remains valid even
    /// if another thread calls update() concurrently.
    [[nodiscard]] std::shared_ptr<const T> read() const {
        return std::atomic_load(&ptr_);
    }

    /// Atomically replace the config with a new snapshot.
    void update(T config) {
        std::atomic_store(&ptr_, std::make_shared<const T>(std::move(config)));
    }

    /// Update via a mutating function (advisory lock pattern).
    /// The mutator receives a copy it can modify; the result is stored atomically.
    template <typename Fn>
    void mutate(Fn&& fn) {
        auto old = read();
        auto updated = std::make_shared<T>(*old);
        fn(*updated);
        std::atomic_store(&ptr_, std::move(updated));
    }

private:
    std::shared_ptr<const T> ptr_;
};

}  // namespace v2::config
