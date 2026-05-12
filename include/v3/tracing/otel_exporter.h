#pragma once
// v3.0.0 D8: Lightweight OpenTelemetry exporter.
// Extends v2 TraceContext to export spans in OTLP-compatible JSON format.
// No external dependencies — builds on existing tracing + nlohmann.

#include "v2/tracing/trace_context.h"

#include <nlohmann/json.hpp>

#include <chrono>
#include <cstdint>
#include <functional>
#include <mutex>
#include <string>
#include <vector>

namespace v3::tracing {

// ── Span record (for export) ───────────────────────────────────────────

struct SpanRecord {
    std::string trace_id_hex;       // 32 hex chars (128-bit W3C)
    std::string span_id_hex;        // 16 hex chars
    std::string parent_span_id_hex;
    std::string operation_name;
    std::uint64_t start_time_unix_ns = 0;
    std::uint64_t duration_ns = 0;
    std::string status;             // "ok", "error"
    std::string service_name;       // "gateway", "login", etc.
};

// ── OtlpExporter ──────────────────────────────────────────────────────

class OtlpExporter {
public:
    struct Config {
        std::string service_name = "boost-gateway";
        std::string export_endpoint;  // e.g., "http://jaeger:4318/v1/traces"
        std::size_t max_batch_size = 256;
        std::chrono::milliseconds export_interval{5000};
    };

    using ExportFn = std::function<bool(const std::string& json_payload)>;

    explicit OtlpExporter(Config config);
    ~OtlpExporter();

    OtlpExporter(const OtlpExporter&) = delete;
    OtlpExporter& operator=(const OtlpExporter&) = delete;

    /// Export a completed Span as an OTLP span record.
    void export_span(const v2::tracing::Span& span,
                     const std::string& service_name = "");

    /// Set a custom export function (default: stores in buffer).
    void set_export_fn(ExportFn fn);

    /// Flush all buffered spans to the export endpoint.
    [[nodiscard]] std::string flush_json();

    /// Flush and clear.
    std::vector<SpanRecord> drain();

    [[nodiscard]] std::size_t buffer_size() const;

private:
    std::string to_hex(std::uint64_t val, int width) const;
    std::string service_name(const std::string& override_name) const;

    Config config_;
    mutable std::mutex mutex_;
    std::vector<SpanRecord> buffer_;
    ExportFn export_fn_;
    std::chrono::steady_clock::time_point last_export_;
};

// ── Implementation ──────────────────────────────────────────────────────

inline OtlpExporter::OtlpExporter(Config config)
    : config_(std::move(config)), last_export_(std::chrono::steady_clock::now()) {}

inline OtlpExporter::~OtlpExporter() = default;

inline std::string OtlpExporter::to_hex(std::uint64_t val, int width) const {
    std::string result(width, '0');
    for (int i = width - 1; i >= 0 && val > 0; --i, val >>= 4) {
        char c = static_cast<char>(val & 0xF);
        result[i] = c < 10 ? '0' + c : 'a' + (c - 10);
    }
    return result;
}

inline std::string OtlpExporter::service_name(
    const std::string& override_name) const {
    return override_name.empty() ? config_.service_name : override_name;
}

inline void OtlpExporter::export_span(
    const v2::tracing::Span& span, const std::string& svc) {
    SpanRecord rec;
    rec.trace_id_hex = to_hex(span.trace_id, 32);
    rec.span_id_hex = to_hex(span.span_id, 16);
    rec.parent_span_id_hex = to_hex(span.parent_span_id, 16);
    rec.operation_name = span.operation_name;

    auto start_ns = std::chrono::duration_cast<std::chrono::nanoseconds>(
        span.start_time.time_since_epoch()).count();
    rec.start_time_unix_ns = static_cast<std::uint64_t>(start_ns);
    rec.duration_ns = static_cast<std::uint64_t>(span.duration_us() * 1000);
    rec.status = span.finished ? "ok" : "error";
    rec.service_name = service_name(svc);

    std::lock_guard lock(mutex_);
    buffer_.push_back(std::move(rec));

    if (buffer_.size() >= config_.max_batch_size) {
        auto payload = flush_json();
        if (export_fn_) export_fn_(payload);
    }
}

inline void OtlpExporter::set_export_fn(ExportFn fn) {
    std::lock_guard lock(mutex_);
    export_fn_ = std::move(fn);
}

inline std::string OtlpExporter::flush_json() {
    nlohmann::json doc;
    nlohmann::json spans = nlohmann::json::array();

    for (const auto& rec : buffer_) {
        spans.push_back({
            {"traceId", rec.trace_id_hex},
            {"spanId", rec.span_id_hex},
            {"parentSpanId", rec.parent_span_id_hex},
            {"operationName", rec.operation_name},
            {"startTimeUnixNano", std::to_string(rec.start_time_unix_ns)},
            {"durationNanos", std::to_string(rec.duration_ns)},
            {"status", rec.status},
            {"serviceName", rec.service_name},
        });
    }
    doc["spans"] = std::move(spans);
    auto json = doc.dump();
    buffer_.clear();
    return json;
}

inline std::vector<SpanRecord> OtlpExporter::drain() {
    std::lock_guard lock(mutex_);
    auto result = std::move(buffer_);
    buffer_.clear();
    return result;
}

inline std::size_t OtlpExporter::buffer_size() const {
    std::lock_guard lock(mutex_);
    return buffer_.size();
}

}  // namespace v3::tracing
