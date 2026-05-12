#pragma once
// v2.3.0: Lightweight message schema validation using nlohmann_json.
// Validates JSON payloads against simple schema definitions
// (required fields, types, value ranges) without external dependencies.

#include <nlohmann/json.hpp>

#include <optional>
#include <string>
#include <string_view>
#include <unordered_map>
#include <vector>

namespace v2::gateway {

struct SchemaField {
    std::string name;
    std::string type;     // "string", "integer", "boolean", "array"
    bool required = true;
    std::optional<std::int64_t> min_value;
    std::optional<std::int64_t> max_value;
};

struct MessageSchema {
    std::string message_type;
    std::vector<SchemaField> fields;
};

struct SchemaValidationResult {
    bool valid = false;
    std::string error;
};

// ── Schema Validator ─────────────────────────────────────────────────────

class SchemaValidator {
public:
    SchemaValidator() { register_defaults(); }

    /// Register a schema for a message type.
    void register_schema(MessageSchema schema) {
        schemas_[schema.message_type] = std::move(schema);
    }

    /// Validate a JSON payload against the schema for a message type.
    [[nodiscard]] SchemaValidationResult validate(
        std::string_view message_type,
        std::string_view json_payload) const;

    /// Quick check: does a message type have a registered schema?
    [[nodiscard]] bool has_schema(std::string_view message_type) const {
        return schemas_.count(std::string(message_type)) > 0;
    }

private:
    void register_defaults();
    std::unordered_map<std::string, MessageSchema> schemas_;
};

// ── Default schemas ──────────────────────────────────────────────────────

inline void SchemaValidator::register_defaults() {
    register_schema(MessageSchema{
        .message_type = "login_request",
        .fields = {
            {.name = "user_id",  .type = "string",  .required = true},
            {.name = "token",    .type = "string",  .required = true},
            {.name = "display_name", .type = "string", .required = false},
        },
    });

    register_schema(MessageSchema{
        .message_type = "room_create",
        .fields = {
            {.name = "user_id",  .type = "string",  .required = true},
            {.name = "room_id",  .type = "string",  .required = true},
        },
    });

    register_schema(MessageSchema{
        .message_type = "room_join",
        .fields = {
            {.name = "user_id",  .type = "string",  .required = true},
            {.name = "room_id",  .type = "string",  .required = true},
        },
    });

    register_schema(MessageSchema{
        .message_type = "room_leave",
        .fields = {
            {.name = "user_id",  .type = "string",  .required = true},
            {.name = "room_id",  .type = "string",  .required = true},
        },
    });

    register_schema(MessageSchema{
        .message_type = "room_ready",
        .fields = {
            {.name = "user_id",  .type = "string",  .required = true},
            {.name = "room_id",  .type = "string",  .required = true},
            {.name = "ready",    .type = "boolean", .required = true},
        },
    });

    register_schema(MessageSchema{
        .message_type = "room_start_battle",
        .fields = {
            {.name = "user_id",  .type = "string",  .required = true},
            {.name = "room_id",  .type = "string",  .required = true},
        },
    });
}

// ── Validation logic ─────────────────────────────────────────────────────

inline SchemaValidationResult SchemaValidator::validate(
    std::string_view message_type,
    std::string_view json_payload) const {

    auto it = schemas_.find(std::string(message_type));
    if (it == schemas_.end()) {
        return {false, "no_schema_registered"};
    }

    const auto& schema = it->second;

    auto doc = nlohmann::json::parse(json_payload, nullptr, false);
    if (doc.is_discarded()) {
        return {false, "invalid_json"};
    }

    for (const auto& field : schema.fields) {
        bool has_field = doc.contains(field.name);

        if (!has_field && field.required) {
            return {false, "missing_required_field:" + field.name};
        }

        if (!has_field) continue;

        const auto& value = doc[field.name];

        // Type checking
        if (field.type == "string" && !value.is_string()) {
            return {false, "type_mismatch:" + field.name + ":expected_string"};
        }
        if (field.type == "integer" && !value.is_number_integer()) {
            return {false, "type_mismatch:" + field.name + ":expected_integer"};
        }
        if (field.type == "boolean" && !value.is_boolean()) {
            return {false, "type_mismatch:" + field.name + ":expected_boolean"};
        }
        if (field.type == "array" && !value.is_array()) {
            return {false, "type_mismatch:" + field.name + ":expected_array"};
        }

        // Value range checking (integers only)
        if (field.type == "integer" && value.is_number_integer()) {
            auto v = value.get<std::int64_t>();
            if (field.min_value.has_value() && v < *field.min_value) {
                return {false, "value_too_small:" + field.name};
            }
            if (field.max_value.has_value() && v > *field.max_value) {
                return {false, "value_too_large:" + field.name};
            }
        }

        // Non-empty string check
        if (field.type == "string" && field.required && value.is_string()) {
            if (value.get<std::string>().empty()) {
                return {false, "empty_field:" + field.name};
            }
        }
    }

    return {true, ""};
}

}  // namespace v2::gateway
