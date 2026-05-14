#pragma once

#include "v2/config/env_util.h"

#include <cstdint>
#include <nlohmann/json.hpp>
#include <optional>
#include <shared_mutex>
#include <string>
#include <unordered_map>

namespace v2::config {

// 单个特性开关配置
struct FeatureFlag {
    std::string name;
    std::uint32_t rollout_percentage = 0;  // 0-100, 0=关闭, 100=全量
    bool enabled = false;                   // 全局开关覆盖
};

class FeatureFlags {
public:
    FeatureFlags() = default;

    // ── 配置管理 ─────────────────────────────────

    // 注册一个特性开关。若已存在则更新。
    void register_flag(std::string name,
                       std::uint32_t rollout_percentage = 0,
                       bool enabled = false) {
        std::unique_lock lock(mutex_);
        auto key = name;
        flags_[std::move(key)] = FeatureFlag{std::move(name), rollout_percentage, enabled};
    }

    // 移除一个特性开关
    void unregister_flag(const std::string& name) {
        std::unique_lock lock(mutex_);
        flags_.erase(name);
    }

    // ── 批量加载与环境覆盖 ───────────────────

    /// Load flags from a JSON object.
    /// Each key is a flag name; value is an object with:
    ///   "enabled": bool (default false)
    ///   "rollout_percentage": uint32_t 0-100 (default 0)
    void load_from_json(const nlohmann::json& json) {
        if (!json.is_object()) return;
        std::unique_lock lock(mutex_);
        for (auto it = json.begin(); it != json.end(); ++it) {
            const auto& name = it.key();
            const auto& entry = it.value();
            bool enabled = entry.value("enabled", false);
            std::uint32_t pct = entry.value("rollout_percentage", 0U);
            if (pct > 100) pct = 100;
            flags_[name] = FeatureFlag{name, pct, enabled};
        }
    }

    /// Apply environment variable overrides for all registered flags.
    /// BOOST_<UPPER_NAME>="1"/"0" overrides enabled.
    /// BOOST_<UPPER_NAME>_ROLLOUT=<N> overrides rollout_percentage.
    /// Env vars take priority over values set via register_flag / load_from_json.
    void apply_env_overrides() {
        std::unique_lock lock(mutex_);
        for (auto& [name, flag] : flags_) {
            auto key = flag_to_env_name(name);
            auto val = get_env(key);
            if (val.has_value()) {
                if (*val == "1") flag.enabled = true;
                else if (*val == "0") flag.enabled = false;
            }
            auto rollout = get_env(key + "_ROLLOUT");
            if (rollout.has_value()) {
                try {
                    auto pct = static_cast<std::uint32_t>(std::stoul(*rollout));
                    if (pct <= 100) flag.rollout_percentage = pct;
                } catch (...) {}
            }
        }
    }

    // 更新灰度百分比（0-100）
    void set_rollout_percentage(const std::string& name, std::uint32_t pct) {
        std::unique_lock lock(mutex_);
        auto it = flags_.find(name);
        if (it != flags_.end()) {
            it->second.rollout_percentage = (pct <= 100) ? pct : 100;
        }
    }

    // 全局开关
    void set_enabled(const std::string& name, bool enabled) {
        std::unique_lock lock(mutex_);
        auto it = flags_.find(name);
        if (it != flags_.end()) {
            it->second.enabled = enabled;
        }
    }

    // ── 用户判定 ─────────────────────────────────

    // 检查指定 user_id 是否启用了该特性。
    // 判定顺序:
    //   1. 如果 enabled == false → 返回 false（全局关闭）
    //   2. 如果 rollout_percentage == 0 → 返回 false
    //   3. 如果 rollout_percentage >= 100 → 返回 true
    //   4. 否则 hash(user_id) % 100 < rollout_percentage → true
    [[nodiscard]] bool is_enabled(const std::string& flag_name,
                                  const std::string& user_id) const {
        std::shared_lock lock(mutex_);
        auto it = flags_.find(flag_name);
        if (it == flags_.end()) return false;
        return is_enabled_impl(it->second, hash_user_id(user_id));
    }

    // 重载: user_id 为整数（如 session_id, actor_id）
    [[nodiscard]] bool is_enabled(const std::string& flag_name,
                                  std::uint64_t user_id) const {
        std::shared_lock lock(mutex_);
        auto it = flags_.find(flag_name);
        if (it == flags_.end()) return false;
        return is_enabled_impl(it->second, hash_user_id(user_id));
    }

    // ── 查询 ─────────────────────────────────────

    [[nodiscard]] std::optional<FeatureFlag> get_flag(const std::string& name) const {
        std::shared_lock lock(mutex_);
        auto it = flags_.find(name);
        if (it != flags_.end()) return it->second;
        return std::nullopt;
    }

    [[nodiscard]] std::size_t flag_count() const {
        std::shared_lock lock(mutex_);
        return flags_.size();
    }

private:
    // FNV-1a hash (simple, fast, good distribution for modulo bucketing)
    static std::uint64_t hash_user_id(const std::string& user_id) {
        std::uint64_t hash = 14695981039346656037ULL;
        for (unsigned char c : user_id) {
            hash ^= c;
            hash *= 1099511628211ULL;
        }
        return hash;
    }

    static std::uint64_t hash_user_id(std::uint64_t user_id) {
        // SplitMix64-style mix for integers
        std::uint64_t h = user_id;
        h ^= h >> 30;
        h *= 0xbf58476d1ce4e5b9ULL;
        h ^= h >> 27;
        h *= 0x94d049bb133111ebULL;
        h ^= h >> 31;
        return h;
    }

    bool is_enabled_impl(const FeatureFlag& flag,
                         std::uint64_t hashed_user_id) const {
        if (!flag.enabled) return false;
        if (flag.rollout_percentage == 0) return false;
        if (flag.rollout_percentage >= 100) return true;
        return (hashed_user_id % 100) < flag.rollout_percentage;
    }

    mutable std::shared_mutex mutex_;
    std::unordered_map<std::string, FeatureFlag> flags_;
};

}  // namespace v2::config
