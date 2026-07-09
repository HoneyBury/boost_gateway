#pragma once

#include "v2/ecs/frame_context.h"
#include "v2/perf/hot_path.h"

#include <string>
#include <vector>

namespace v2::ecs {

class World;

struct SystemMetadata {
    std::string name;
    std::vector<std::string> dependencies{};  // stage dependency names
};

class System {
public:
    virtual ~System() = default;
    BOOST_HOT_PATH virtual void run(World& world, const FrameContext& ctx) = 0;
    virtual SystemMetadata metadata() const { return {"unknown", {}}; }
};

}  // namespace v2::ecs
