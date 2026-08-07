#pragma once

#include <chrono>
#include <cstdint>
#include <memory>
#include <string>
#include <type_traits>
#include <unordered_map>
#include <utility>
#include <vector>

#include "v2/ecs/entity.h"
#include "v2/ecs/component.h"
#include "v2/ecs/system.h"
#include "v2/memory/arena.h"
#include "v2/memory/object_pool.h"

namespace v2::ecs {

class SystemExecutor;

class World {
public:
    World() = default;
    virtual ~World() = default;

    World(const World&) = delete;
    World& operator=(const World&) = delete;

    virtual EntityHandle create_entity() = 0;
    virtual void destroy_entity(EntityHandle entity) = 0;
    [[nodiscard]] virtual bool exists(EntityHandle entity) const = 0;

    template <typename T, typename... Args>
    T& add_component(EntityHandle entity, Args&&... args) {
        static_assert(std::is_base_of_v<Component, T>,
                      "T must derive from v2::ecs::Component");
        auto component = std::make_unique<T>(std::forward<Args>(args)...);
        auto* stored = add_component_erased(entity, component_type_id<T>(), std::move(component));
        return static_cast<T&>(*stored);
    }

    template <typename T>
    [[nodiscard]] T* get_component(EntityHandle entity) {
        static_assert(std::is_base_of_v<Component, T>,
                      "T must derive from v2::ecs::Component");
        return static_cast<T*>(get_component_erased(entity, component_type_id<T>()));
    }

    template <typename T>
    bool remove_component(EntityHandle entity) {
        static_assert(std::is_base_of_v<Component, T>,
                      "T must derive from v2::ecs::Component");
        return remove_component_erased(entity, component_type_id<T>());
    }

    virtual void tick(const FrameContext& ctx) = 0;
    virtual void set_executor(std::unique_ptr<SystemExecutor> executor) = 0;

protected:
    virtual Component* add_component_erased(EntityHandle entity,
                                            ComponentTypeId type_id,
                                            std::unique_ptr<Component> component) = 0;
    [[nodiscard]] virtual Component* get_component_erased(EntityHandle entity,
                                                          ComponentTypeId type_id) = 0;
    virtual bool remove_component_erased(EntityHandle entity, ComponentTypeId type_id) = 0;
};

class SimpleWorld final : public World {
public:
    SimpleWorld();
    ~SimpleWorld() override;

    EntityHandle create_entity() override;
    void destroy_entity(EntityHandle entity) override;
    [[nodiscard]] bool exists(EntityHandle entity) const override;
    void tick(const FrameContext& ctx) override;
    void set_executor(std::unique_ptr<SystemExecutor> executor) override;

    void add_system(std::unique_ptr<System> system);

    void set_allocator(std::unique_ptr<v2::memory::BumpArena> arena);

    template <typename T, typename Fn>
    void for_each(Fn&& fn) {
        static_assert(std::is_base_of_v<Component, T>,
                      "T must derive from v2::ecs::Component");
        auto* store = find_store(component_type_id<T>());
        if (store == nullptr) {
            return;
        }
        for (auto& entry : store->dense) {
            const auto entity_key = entry.entity_key;
            const EntityHandle handle{
                .id = static_cast<EntityId>(entity_key),
                .generation = static_cast<std::uint32_t>(entity_key >> 32U),
            };
            fn(handle, static_cast<T&>(*entry.component));
        }
    }

private:
    using ComponentEntityKey = std::uint64_t;

    struct ComponentStore {
        struct Entry {
            ComponentEntityKey entity_key = 0;
            std::unique_ptr<Component> component;
        };

        Component* insert(ComponentEntityKey key,
                          std::unique_ptr<Component> component);
        [[nodiscard]] Component* find(ComponentEntityKey key) noexcept;
        [[nodiscard]] const Component* find(ComponentEntityKey key) const noexcept;
        bool erase(ComponentEntityKey key) noexcept;

        std::vector<Entry> dense;
        std::unordered_map<ComponentEntityKey, std::size_t> index_by_entity;
    };

    [[nodiscard]] static constexpr ComponentEntityKey component_entity_key(
        EntityHandle entity) noexcept {
        return (static_cast<ComponentEntityKey>(entity.generation) << 32U) |
               static_cast<ComponentEntityKey>(entity.id);
    }

    Component* add_component_erased(EntityHandle entity,
                                    ComponentTypeId type_id,
                                    std::unique_ptr<Component> component) override;
    [[nodiscard]] Component* get_component_erased(EntityHandle entity,
                                                  ComponentTypeId type_id) override;
    bool remove_component_erased(EntityHandle entity, ComponentTypeId type_id) override;

    [[nodiscard]] ComponentStore* find_store(ComponentTypeId type_id);
    [[nodiscard]] const ComponentStore* find_store(ComponentTypeId type_id) const;

    struct EntityStorage {
        std::uint32_t generation;
        bool arena_allocated;
    };

    std::unordered_map<ComponentTypeId, ComponentStore> component_stores_;
    std::unordered_map<EntityId, EntityStorage*> entity_storage_;
    std::unordered_map<EntityId, EntityHandle*> handle_map_;
    std::vector<std::unique_ptr<System>> systems_;
    std::unique_ptr<SystemExecutor> executor_;
    EntityId next_entity_id_ = 1;

    std::unique_ptr<v2::memory::BumpArena> arena_;
    v2::memory::ObjectPool<EntityHandle> handle_pool_;
};

}  // namespace v2::ecs
