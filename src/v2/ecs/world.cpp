#include "v2/ecs/world.h"
#include "v2/ecs/parallel_system_executor.h"

#include <utility>

namespace v2::ecs {

SimpleWorld::SimpleWorld() = default;

SimpleWorld::~SimpleWorld() {
    for (auto& [id, storage] : entity_storage_) {
        if (storage && !storage->arena_allocated) {
            delete storage;
        }
    }
}

void SimpleWorld::set_allocator(std::unique_ptr<v2::memory::BumpArena> arena) {
    arena_ = std::move(arena);
}

EntityHandle SimpleWorld::create_entity() {
    const auto entity_id = next_entity_id_++;

    // Allocate entity storage (arena if available, otherwise heap)
    EntityStorage* storage = nullptr;
    if (arena_) {
        storage = static_cast<EntityStorage*>(arena_->alloc(sizeof(EntityStorage)));
    }
    if (storage) {
        storage->generation = 1;
        storage->arena_allocated = true;
    } else {
        storage = new EntityStorage{1, false};
    }
    entity_storage_[entity_id] = storage;

    // Acquire handle from pool
    EntityHandle* slot = handle_pool_.acquire();
    if (slot) {
        slot->id = entity_id;
        slot->generation = storage->generation;
        handle_map_[entity_id] = slot;
        return *slot;
    }

    return EntityHandle{.id = entity_id, .generation = storage->generation};
}

void SimpleWorld::destroy_entity(EntityHandle entity) {
    if (!exists(entity)) {
        return;
    }
    for (auto& [type_id, store] : component_stores_) {
        (void)type_id;
        store.components.erase(component_entity_key(entity));
    }

    // Free entity storage
    auto sit = entity_storage_.find(entity.id);
    if (sit != entity_storage_.end()) {
        if (!sit->second->arena_allocated) {
            delete sit->second;
        }
        entity_storage_.erase(sit);
    }

    // Release handle back to pool
    auto hit = handle_map_.find(entity.id);
    if (hit != handle_map_.end()) {
        handle_pool_.release(hit->second);
        handle_map_.erase(hit);
    }
}

bool SimpleWorld::exists(EntityHandle entity) const {
    if (!entity.valid()) {
        return false;
    }
    const auto it = entity_storage_.find(entity.id);
    return it != entity_storage_.end() && it->second->generation == entity.generation;
}

void SimpleWorld::tick(const FrameContext& ctx) {
    if (executor_) {
        executor_->execute_all(*this, ctx);
        return;
    }
    for (auto& system : systems_) {
        system->run(*this, ctx);
    }
}

void SimpleWorld::add_system(std::unique_ptr<System> system) {
    if (!system) {
        return;
    }
    if (executor_) {
        executor_->add_system(std::move(system), system->metadata());
        return;
    }
    systems_.push_back(std::move(system));
}

void SimpleWorld::set_executor(std::unique_ptr<SystemExecutor> executor) {
    executor_ = std::move(executor);
    for (auto& sys : systems_) {
        executor_->add_system(std::move(sys), sys ? sys->metadata() : SystemMetadata{});
    }
    systems_.clear();
}

Component* SimpleWorld::add_component_erased(EntityHandle entity,
                                             ComponentTypeId type_id,
                                             std::unique_ptr<Component> component) {
    if (!exists(entity) || component == nullptr) {
        return nullptr;
    }
    auto& slot = component_stores_[type_id].components[component_entity_key(entity)];
    slot = std::move(component);
    return slot.get();
}

Component* SimpleWorld::get_component_erased(EntityHandle entity,
                                             ComponentTypeId type_id) {
    if (!exists(entity)) {
        return nullptr;
    }
    auto* store = find_store(type_id);
    if (store == nullptr) {
        return nullptr;
    }
    auto it = store->components.find(component_entity_key(entity));
    return it == store->components.end() ? nullptr : it->second.get();
}

bool SimpleWorld::remove_component_erased(EntityHandle entity, ComponentTypeId type_id) {
    if (!exists(entity)) {
        return false;
    }
    auto* store = find_store(type_id);
    if (store == nullptr) {
        return false;
    }
    return store->components.erase(component_entity_key(entity)) > 0;
}

SimpleWorld::ComponentStore* SimpleWorld::find_store(ComponentTypeId type_id) {
    auto it = component_stores_.find(type_id);
    return it == component_stores_.end() ? nullptr : &it->second;
}

const SimpleWorld::ComponentStore* SimpleWorld::find_store(ComponentTypeId type_id) const {
    auto it = component_stores_.find(type_id);
    return it == component_stores_.end() ? nullptr : &it->second;
}

}  // namespace v2::ecs
