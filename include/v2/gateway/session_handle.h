#pragma once

#include <compare>
#include <cstdint>

namespace v2::gateway {

/// Lightweight non-owning handle representing a gateway session.
/// Replaces bare shared_ptr<net::Session> in the v2 PacketBridge interface
/// so that v2 code does not depend on v1 session types.
class SessionHandle {
public:
    using IdType = std::uint64_t;

    constexpr SessionHandle() noexcept : id_(0) {}

    constexpr explicit SessionHandle(IdType id) noexcept : id_(id) {}

    [[nodiscard]] constexpr IdType id() const noexcept { return id_; }

    [[nodiscard]] constexpr explicit operator IdType() const noexcept { return id_; }

    [[nodiscard]] constexpr bool valid() const noexcept { return id_ != 0; }

    constexpr auto operator<=>(const SessionHandle&) const = default;

private:
    IdType id_{0};
};

}  // namespace v2::gateway
