#pragma once

#include "v2/actor/message.h"  // for TraceId

#include <atomic>
#include <chrono>
#include <cstdint>
#include <string>

namespace v2::tracing {

// ── Span Identity ──────────────────────────────────

using SpanId = std::uint64_t;

// 全局 SpanId 生成器（单调递增，线程安全）
inline SpanId generate_span_id() {
    static std::atomic<SpanId> counter{1};
    return counter.fetch_add(1, std::memory_order_relaxed);
}

// 全局 TraceId 生成器
inline v2::actor::TraceId generate_trace_id() {
    static std::atomic<v2::actor::TraceId> counter{1};
    return counter.fetch_add(1, std::memory_order_relaxed);
}

// ── Span ────────────────────────────────────────────

// Span 表示追踪树中的一个操作节点。
// 构造时自动生成 span_id 并记录开始时间。
struct Span {
    SpanId span_id = 0;
    SpanId parent_span_id = 0;
    v2::actor::TraceId trace_id = 0;
    std::string operation_name;
    std::chrono::steady_clock::time_point start_time;
    std::chrono::steady_clock::time_point end_time;
    bool finished = false;

    // 创建一个新的根 Span（无父 span）
    static Span root(std::string operation) {
        Span s;
        s.trace_id = generate_trace_id();
        s.span_id = generate_span_id();
        s.parent_span_id = 0;
        s.operation_name = std::move(operation);
        s.start_time = std::chrono::steady_clock::now();
        return s;
    }

    // 创建一个子 Span
    static Span child(const Span& parent, std::string operation) {
        Span s;
        s.trace_id = parent.trace_id;
        s.span_id = generate_span_id();
        s.parent_span_id = parent.span_id;
        s.operation_name = std::move(operation);
        s.start_time = std::chrono::steady_clock::now();
        return s;
    }

    // 从已有的 trace_id 创建 span（用于跨进程续接追踪）
    static Span from_trace(v2::actor::TraceId existing_trace_id,
                           SpanId parent_span,
                           std::string operation) {
        Span s;
        s.trace_id = existing_trace_id;
        s.span_id = generate_span_id();
        s.parent_span_id = parent_span;
        s.operation_name = std::move(operation);
        s.start_time = std::chrono::steady_clock::now();
        return s;
    }

    // 结束 span，记录结束时间
    void finish() {
        if (!finished) {
            end_time = std::chrono::steady_clock::now();
            finished = true;
        }
    }

    // 自动结束（用于 RAII helper）
    ~Span() { finish(); }

    // 可移动
    Span(Span&& other) noexcept
        : span_id(other.span_id),
          parent_span_id(other.parent_span_id),
          trace_id(other.trace_id),
          operation_name(std::move(other.operation_name)),
          start_time(other.start_time),
          end_time(other.end_time),
          finished(other.finished) {
        other.finished = true;  // 原对象不再负责结束
    }

    Span& operator=(Span&& other) noexcept {
        if (this != &other) {
            finish();
            span_id = other.span_id;
            parent_span_id = other.parent_span_id;
            trace_id = other.trace_id;
            operation_name = std::move(other.operation_name);
            start_time = other.start_time;
            end_time = other.end_time;
            finished = other.finished;
            other.finished = true;
        }
        return *this;
    }

    // 不可拷贝
    Span(const Span&) = delete;
    Span& operator=(const Span&) = delete;

    // 默认构造（用于容器/optional）
    Span() = default;

    // 持续时间（微秒）
    [[nodiscard]] std::int64_t duration_us() const {
        auto end = finished ? end_time : std::chrono::steady_clock::now();
        return std::chrono::duration_cast<std::chrono::microseconds>(
                   end - start_time)
            .count();
    }
};

// ── TraceContext ────────────────────────────────────

// TraceContext 携带跨操作/跨 Actor/跨服务的追踪状态。
// 它是轻量级值类型，可以嵌入 MessageHeader 或通过 lambda 捕获传递。
struct TraceContext {
    v2::actor::TraceId trace_id = 0;
    SpanId current_span_id = 0;

    // 从已有的 trace_id + span_id 构造（续接已有追踪链）
    static TraceContext from_message(v2::actor::TraceId trace_id,
                                     SpanId span_id) {
        return TraceContext{trace_id, span_id};
    }

    // 创建新的追踪根
    static TraceContext create_root() {
        auto tid = generate_trace_id();
        auto sid = generate_span_id();
        return TraceContext{tid, sid};
    }

    // 从 MessageHeader 提取 TraceContext
    static TraceContext from_header(const v2::actor::MessageHeader& header) {
        return TraceContext{header.trace_id, 0};
    }

    // 将 TraceContext 应用到 MessageHeader（在发送消息前调用）
    void apply_to_header(v2::actor::MessageHeader& header) const {
        header.trace_id = trace_id;
    }

    // 为此 context 创建一个新的子 span
    [[nodiscard]] Span begin_span(std::string operation) const {
        Span s;
        s.trace_id = trace_id;
        s.span_id = generate_span_id();
        s.parent_span_id = current_span_id;
        s.operation_name = std::move(operation);
        s.start_time = std::chrono::steady_clock::now();
        return s;
    }

    // ── W3C TraceContext (traceparent header) ──────────
    // Format: "00-{trace_id_hex(32)}-{span_id_hex(16)}-{trace_flags(2)}"
    // trace_flags: "01" = sampled

    [[nodiscard]] std::string to_w3c_traceparent() const {
        char buf[56];
        std::snprintf(buf, sizeof(buf), "00-%016llx%016llx-%016llx-01",
                      static_cast<unsigned long long>(trace_id),
                      static_cast<unsigned long long>(0),
                      static_cast<unsigned long long>(current_span_id));
        return buf;
    }

    static TraceContext from_w3c_traceparent(const std::string& header) {
        TraceContext ctx{0, 0};
        if (header.size() < 55 || header[0] != '0' || header[1] != '0' || header[2] != '-') {
            return ctx;
        }
        // Parse trace_id (32 hex chars)
        ctx.trace_id = std::strtoull(header.substr(3, 16).c_str(), nullptr, 16);
        // Skip parent_span_id (16 hex chars at position 36)
        // Parse span_id at position 36
        ctx.current_span_id = std::strtoull(header.substr(36, 16).c_str(), nullptr, 16);
        return ctx;
    }
};

}  // namespace v2::tracing
