#pragma once
// v3.0.0 D8: Lightweight OpenTelemetry exporter.
// Extends v2 TraceContext to export spans in OTLP-compatible JSON format.

#include "v2/tracing/trace_context.h"

#include <boost/asio/connect.hpp>
#include <boost/asio/io_context.hpp>
#include <boost/beast/core/flat_buffer.hpp>
#include <boost/beast/core/tcp_stream.hpp>
#include <boost/beast/http/message.hpp>
#include <boost/beast/http/read.hpp>
#include <boost/beast/http/string_body.hpp>
#include <boost/beast/http/write.hpp>
#include <nlohmann/json.hpp>

#include <chrono>
#include <cstdint>
#include <functional>
#include <iterator>
#include <mutex>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace v3::tracing {

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

class OtlpExporter {
public:
    struct Config {
        std::string service_name = "boost-gateway";
        std::string export_endpoint;  // e.g. "http://jaeger:4318/v1/traces"
        std::size_t max_batch_size = 256;
        std::chrono::milliseconds export_interval{5000};
    };

    using ExportFn = std::function<bool(const std::string& json_payload)>;

    struct Metrics {
        std::uint64_t enqueued_spans = 0;
        std::uint64_t exported_spans = 0;
        std::uint64_t successful_batches = 0;
        std::uint64_t failed_batches = 0;
        std::uint64_t buffered_spans = 0;
    };

    explicit OtlpExporter(Config config);
    ~OtlpExporter();

    OtlpExporter(const OtlpExporter&) = delete;
    OtlpExporter& operator=(const OtlpExporter&) = delete;

    void export_span(const v2::tracing::Span& span,
                     const std::string& service_name = "");

    void set_export_fn(ExportFn fn);

    [[nodiscard]] bool flush();
    [[nodiscard]] std::string flush_json();
    std::vector<SpanRecord> drain();
    [[nodiscard]] std::size_t buffer_size() const;
    [[nodiscard]] Metrics metrics() const;

private:
    [[nodiscard]] std::string to_hex(std::uint64_t val, int width) const;
    [[nodiscard]] std::string service_name(
        const std::string& override_name) const;
    [[nodiscard]] std::string serialize_records(
        const std::vector<SpanRecord>& records) const;
    [[nodiscard]] bool post_json(const std::string& json_payload) const;
    [[nodiscard]] bool attempt_export(std::vector<SpanRecord> batch, const ExportFn& export_fn);

    Config config_;
    mutable std::mutex mutex_;
    std::vector<SpanRecord> buffer_;
    std::size_t in_flight_spans_ = 0;
    std::uint64_t enqueued_spans_ = 0;
    std::uint64_t exported_spans_ = 0;
    std::uint64_t successful_batches_ = 0;
    std::uint64_t failed_batches_ = 0;
    ExportFn export_fn_;
    std::chrono::steady_clock::time_point last_export_;
};

namespace beast = boost::beast;
namespace http = beast::http;
using tcp = boost::asio::ip::tcp;

inline OtlpExporter::OtlpExporter(Config config)
    : config_(std::move(config)),
      last_export_(std::chrono::steady_clock::now()) {
    if (!config_.export_endpoint.empty()) {
        export_fn_ = [this](const std::string& payload) {
            return post_json(payload);
        };
    }
}

inline OtlpExporter::~OtlpExporter() = default;

inline std::string OtlpExporter::to_hex(std::uint64_t val, int width) const {
    std::string result(width, '0');
    for (int i = width - 1; i >= 0 && val > 0; --i, val >>= 4) {
        const char c = static_cast<char>(val & 0xF);
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

    const auto start_ns = std::chrono::duration_cast<std::chrono::nanoseconds>(
        span.start_time.time_since_epoch()).count();
    rec.start_time_unix_ns = static_cast<std::uint64_t>(start_ns);
    rec.duration_ns = static_cast<std::uint64_t>(span.duration_us() * 1000);
    rec.status = span.finished ? "ok" : "error";
    rec.service_name = service_name(svc);

    std::vector<SpanRecord> batch;
    ExportFn export_fn;
    {
        std::lock_guard lock(mutex_);
        buffer_.push_back(std::move(rec));
        ++enqueued_spans_;
        const auto now = std::chrono::steady_clock::now();
        const bool batch_full =
            export_fn_ && buffer_.size() >= config_.max_batch_size;
        const bool interval_elapsed =
            export_fn_ && (now - last_export_ >= config_.export_interval);
        if (batch_full || interval_elapsed) {
            batch = std::move(buffer_);
            buffer_.clear();
            in_flight_spans_ += batch.size();
            last_export_ = now;
            export_fn = export_fn_;
        }
    }

    if (!batch.empty() && export_fn) {
        (void)attempt_export(std::move(batch), export_fn);
    }
}

inline void OtlpExporter::set_export_fn(ExportFn fn) {
    std::lock_guard lock(mutex_);
    export_fn_ = std::move(fn);
}

inline bool OtlpExporter::flush() {
    std::vector<SpanRecord> batch;
    ExportFn export_fn;
    {
        std::lock_guard lock(mutex_);
        if (buffer_.empty()) {
            return true;
        }
        if (!export_fn_) {
            return false;
        }
        batch = std::move(buffer_);
        buffer_.clear();
        in_flight_spans_ += batch.size();
        last_export_ = std::chrono::steady_clock::now();
        export_fn = export_fn_;
    }

    return attempt_export(std::move(batch), export_fn);
}

inline bool OtlpExporter::attempt_export(std::vector<SpanRecord> batch, const ExportFn& export_fn) {
    bool succeeded = false;
    try {
        succeeded = export_fn(serialize_records(batch));
    } catch (...) {
        succeeded = false;
    }

    std::lock_guard lock(mutex_);
    in_flight_spans_ -= batch.size();
    if (succeeded) {
        exported_spans_ += batch.size();
        ++successful_batches_;
        return true;
    }
    ++failed_batches_;
    buffer_.insert(buffer_.begin(),
                   std::make_move_iterator(batch.begin()),
                   std::make_move_iterator(batch.end()));
    return false;
}

inline std::string OtlpExporter::serialize_records(
    const std::vector<SpanRecord>& records) const {
    nlohmann::json spans = nlohmann::json::array();
    for (const auto& rec : records) {
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
    return nlohmann::json{{"spans", std::move(spans)}}.dump();
}

inline std::string OtlpExporter::flush_json() {
    std::lock_guard lock(mutex_);
    const auto json = serialize_records(buffer_);
    if (!buffer_.empty()) {
        exported_spans_ += buffer_.size();
        ++successful_batches_;
    }
    buffer_.clear();
    return json;
}

inline std::vector<SpanRecord> OtlpExporter::drain() {
    std::lock_guard lock(mutex_);
    auto result = std::move(buffer_);
    if (!result.empty()) {
        exported_spans_ += result.size();
        ++successful_batches_;
    }
    buffer_.clear();
    return result;
}

inline std::size_t OtlpExporter::buffer_size() const {
    std::lock_guard lock(mutex_);
    return buffer_.size();
}

inline OtlpExporter::Metrics OtlpExporter::metrics() const {
    std::lock_guard lock(mutex_);
    return Metrics{
        .enqueued_spans = enqueued_spans_,
        .exported_spans = exported_spans_,
        .successful_batches = successful_batches_,
        .failed_batches = failed_batches_,
        .buffered_spans = static_cast<std::uint64_t>(buffer_.size() + in_flight_spans_),
    };
}

inline bool OtlpExporter::post_json(const std::string& json_payload) const {
    std::string_view endpoint = config_.export_endpoint;
    if (!endpoint.starts_with("http://")) {
        return false;
    }
    endpoint.remove_prefix(7);

    std::string host;
    std::string port = "80";
    std::string target = "/";
    std::string_view host_port = endpoint;
    const auto slash_pos = endpoint.find('/');
    if (slash_pos != std::string_view::npos) {
        host_port = endpoint.substr(0, slash_pos);
        target = std::string(endpoint.substr(slash_pos));
    }
    const auto colon_pos = host_port.find(':');
    if (colon_pos == std::string_view::npos) {
        host = std::string(host_port);
    } else {
        host = std::string(host_port.substr(0, colon_pos));
        port = std::string(host_port.substr(colon_pos + 1));
    }
    if (host.empty()) {
        return false;
    }

    try {
        boost::asio::io_context io_context;
        tcp::resolver resolver(io_context);
        beast::tcp_stream stream(io_context);
        auto endpoints = resolver.resolve(host, port);
        stream.connect(endpoints);

        http::request<http::string_body> request{http::verb::post, target, 11};
        request.set(http::field::host, host);
        request.set(http::field::content_type, "application/json");
        request.set(http::field::user_agent, "boost-gateway-otlp");
        request.body() = json_payload;
        request.prepare_payload();

        http::write(stream, request);

        beast::flat_buffer response_buffer;
        http::response<http::string_body> response;
        http::read(stream, response_buffer, response);

        beast::error_code ec;
        stream.socket().shutdown(tcp::socket::shutdown_both, ec);
        return response.result_int() >= 200 && response.result_int() < 300;
    } catch (...) {
        return false;
    }
}

}  // namespace v3::tracing
