#pragma once

#include "net/packet_codec.h"

#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <unordered_map>
#include <vector>

namespace net::packet {

constexpr std::size_t kFragmentThreshold = 8192;   // fragment packets > 8KB
constexpr std::size_t kFragmentPayloadSize = 4096;  // each fragment carries 4KB

// Fragment flags stored in the flags byte upper nibble
namespace fragment_flags {
constexpr std::uint8_t kFragment = 0x10;   // This packet is a fragment
constexpr std::uint8_t kLastFragment = 0x20;  // Last fragment in sequence
}  // namespace fragment_flags

struct FragmentAssembly {
    std::uint16_t message_id = 0;
    std::uint32_t request_id = 0;
    std::int32_t error_code = 0;
    std::uint8_t fragment_count = 0;
    std::uint8_t fragments_received = 0;
    std::string body;
};

class FragmentAssembler {
public:
    std::optional<DecodedPacket> feed(const DecodedPacket& fragment) {
        if (!(fragment.flags & fragment_flags::kFragment)) {
            // Not a fragment — pass through
            return fragment;
        }

        const bool is_last = (fragment.flags & fragment_flags::kLastFragment) != 0;
        const auto total_count = (fragment.flags >> 4) & 0x07;  // fragment count in upper nibble bits
        const auto frag_index = fragment.flags & 0x03;            // fragment index in lower bits

        auto& assembly = assemblies_[fragment.request_id];
        if (frag_index == 0) {
            assembly.message_id = fragment.message_id;
            assembly.request_id = fragment.request_id;
            assembly.error_code = fragment.error_code;
            assembly.fragment_count = static_cast<std::uint8_t>(total_count);
            assembly.fragments_received = 0;
            assembly.body.clear();
        }

        assembly.body.append(fragment.body);
        assembly.fragments_received++;

        if (is_last || assembly.fragments_received >= assembly.fragment_count) {
            DecodedPacket complete{
                .message_id = assembly.message_id,
                .request_id = assembly.request_id,
                .error_code = assembly.error_code,
                .flags = 0,
                .body = std::move(assembly.body),
            };
            assemblies_.erase(fragment.request_id);
            return complete;
        }

        return std::nullopt;
    }

private:
    std::unordered_map<std::uint32_t, FragmentAssembly> assemblies_;
};

inline std::vector<DecodedPacket> fragment_packet(std::uint16_t message_id,
                                                    std::uint32_t request_id,
                                                    std::int32_t error_code,
                                                    std::string_view body) {
    if (body.size() <= kFragmentThreshold) {
        return {DecodedPacket{message_id, request_id, error_code, 0, std::string(body)}};
    }

    std::vector<DecodedPacket> fragments;
    const auto total_fragments =
        static_cast<std::uint8_t>((body.size() + kFragmentPayloadSize - 1) / kFragmentPayloadSize);

    for (std::uint8_t i = 0; i < total_fragments; ++i) {
        const auto offset = static_cast<std::size_t>(i) * kFragmentPayloadSize;
        const auto len = std::min(kFragmentPayloadSize, body.size() - offset);
        std::uint8_t flags = fragment_flags::kFragment | (total_fragments << 4) | (i & 0x03);
        if (i == total_fragments - 1) flags |= fragment_flags::kLastFragment;

        fragments.push_back(DecodedPacket{
            message_id, request_id, error_code, flags,
            std::string(body.substr(offset, len)),
        });
    }
    return fragments;
}

}  // namespace net::packet
