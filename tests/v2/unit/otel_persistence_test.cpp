// v3.0.0 Phase 16: OpenTelemetry exporter + Event store tests

#include <gtest/gtest.h>
#include "v3/tracing/otel_exporter.h"
#include "v3/persistence/event_store.h"
#include <fstream>
#include <thread>

using namespace v3::tracing;
using namespace v3::persistence;

// ─── OpenTelemetry Exporter ──────────────────────────────────────────────

TEST(OtelExporterTest, ExportSingleSpan) {
    OtlpExporter::Config config{.service_name = "test-service"};
    OtlpExporter exporter(config);

    auto span = v2::tracing::Span::root("test_operation");
    span.finish();
    exporter.export_span(span);

    EXPECT_EQ(exporter.buffer_size(), 1U);

    auto json = exporter.flush_json();
    EXPECT_FALSE(json.empty());
    EXPECT_NE(json.find("test_operation"), std::string::npos);
    EXPECT_NE(json.find("spanId"), std::string::npos);
    EXPECT_EQ(exporter.buffer_size(), 0U);  // flushed
}

TEST(OtelExporterTest, ExportMultipleSpans) {
    OtlpExporter exporter({"multi-test"});

    for (int i = 0; i < 5; ++i) {
        auto span = v2::tracing::Span::root("op_" + std::to_string(i));
        span.finish();
        exporter.export_span(span);
    }
    EXPECT_EQ(exporter.buffer_size(), 5U);

    auto records = exporter.drain();
    EXPECT_EQ(records.size(), 5U);
    EXPECT_EQ(exporter.buffer_size(), 0U);
}

TEST(OtelExporterTest, CustomExportFunction) {
    OtlpExporter exporter({
        .service_name = "fn-test",
        .max_batch_size = 1,
    });
    int export_count = 0;
    exporter.set_export_fn([&](const std::string&) {
        ++export_count;
        return true;
    });

    auto span = v2::tracing::Span::root("export_test");
    span.finish();
    exporter.export_span(span);

    EXPECT_EQ(export_count, 1);
    EXPECT_EQ(exporter.buffer_size(), 0U);
}

TEST(OtelExporterTest, FlushRequeuesWhenExportFails) {
    OtlpExporter exporter({"flush-test"});
    exporter.set_export_fn([](const std::string&) {
        return false;
    });

    auto span = v2::tracing::Span::root("flush_retry");
    span.finish();
    exporter.export_span(span);

    EXPECT_FALSE(exporter.flush());
    EXPECT_EQ(exporter.buffer_size(), 1U);
}

TEST(OtelExporterTest, SpanRecordFields) {
    OtlpExporter exporter({"field-test"});

    auto span = v2::tracing::Span::root("field_op");
    span.finish();
    exporter.export_span(span, "login");

    auto records = exporter.drain();
    ASSERT_EQ(records.size(), 1U);
    EXPECT_FALSE(records[0].trace_id_hex.empty());
    EXPECT_FALSE(records[0].span_id_hex.empty());
    EXPECT_EQ(records[0].operation_name, "field_op");
    EXPECT_EQ(records[0].service_name, "login");
}

// ─── Event Store (InMemory) ──────────────────────────────────────────────

TEST(EventStoreTest, AppendAndRead) {
    InMemoryEventStore store;

    EventRecord ev{.event_type = "battle_result",
                    .aggregate_id = "battle_001",
                    .payload = R"({"winner":"alice"})"};
    ASSERT_TRUE(store.append(ev));

    auto events = store.read("battle_001");
    ASSERT_EQ(events.size(), 1U);
    EXPECT_EQ(events[0].event_type, "battle_result");
    EXPECT_EQ(events[0].payload, R"({"winner":"alice"})");
}

TEST(EventStoreTest, ReadWithSequenceFilter) {
    InMemoryEventStore store;
    store.append(EventRecord{.event_type = "login", .aggregate_id = "user_1", .payload = "v1"});
    store.append(EventRecord{.event_type = "login", .aggregate_id = "user_1", .payload = "v2"});
    store.append(EventRecord{.event_type = "login", .aggregate_id = "user_1", .payload = "v3"});

    // Read from sequence 2
    auto events = store.read("user_1", 2);
    ASSERT_EQ(events.size(), 2U);
    EXPECT_EQ(events[0].payload, "v2");
    EXPECT_EQ(events[1].payload, "v3");
}

TEST(EventStoreTest, LatestSequence) {
    InMemoryEventStore store;
    store.append(EventRecord{.event_type = "move", .aggregate_id = "battle_001", .payload = "1"});
    store.append(EventRecord{.event_type = "move", .aggregate_id = "battle_001", .payload = "2"});
    store.append(EventRecord{.event_type = "move", .aggregate_id = "battle_002", .payload = "1"});

    EXPECT_EQ(store.latest_sequence("battle_001"), 2U);
    EXPECT_EQ(store.latest_sequence("battle_002"), 3U);
    EXPECT_EQ(store.latest_sequence("nonexistent"), 0U);
}

TEST(EventStoreTest, ReadByType) {
    InMemoryEventStore store;
    store.append(EventRecord{.event_type = "battle_result", .aggregate_id = "b1", .payload = "r1"});
    store.append(EventRecord{.event_type = "login", .aggregate_id = "u1", .payload = "l1"});
    store.append(EventRecord{.event_type = "battle_result", .aggregate_id = "b2", .payload = "r2"});

    auto battles = store.read_by_type("battle_result");
    ASSERT_EQ(battles.size(), 2U);
    EXPECT_EQ(battles[0].aggregate_id, "b1");
    EXPECT_EQ(battles[1].aggregate_id, "b2");
}

TEST(EventStoreTest, TotalEventsCounter) {
    InMemoryEventStore store;
    store.append(EventRecord{.event_type = "e1", .aggregate_id = "a", .payload = "{}"});
    store.append(EventRecord{.event_type = "e2", .aggregate_id = "b", .payload = "{}"});

    EXPECT_EQ(store.total_events(), 2U);
}

TEST(EventStoreTest, TraceIdPropagation) {
    InMemoryEventStore store;
    EventRecord ev{.event_type = "battle_input",
                    .aggregate_id = "battle_001",
                    .payload = "move:1,2",
                    .trace_id = 0xABCD1234};
    store.append(ev);

    auto events = store.read("battle_001");
    ASSERT_EQ(events.size(), 1U);
    EXPECT_EQ(events[0].trace_id, 0xABCD1234U);
}
