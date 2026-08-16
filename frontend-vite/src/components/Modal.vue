<template>
  <Teleport to="body">
    <transition name="fade">
      <div v-if="visible" class="modal-mask" @click.self="$emit('close')">
        <div class="modal" :style="{ width }">
          <div class="modal-head">
            <h3>{{ title }}</h3>
            <button class="btn btn-ghost btn-sm" @click="$emit('close')">✕</button>
          </div>
          <div class="modal-body">
            <slot />
          </div>
        </div>
      </div>
    </transition>
  </Teleport>
</template>

<script setup>
defineProps({
  visible: Boolean,
  title: { type: String, default: '' },
  width: { type: String, default: '560px' },
})
defineEmits(['close'])
</script>

<style scoped>
.modal-mask {
  position: fixed; inset: 0; background: rgba(15,23,42,.55); z-index: 2000;
  display: flex; align-items: center; justify-content: center; padding: 24px;
}
.modal {
  background: #fff; border-radius: 12px; box-shadow: 0 20px 50px rgba(0,0,0,.25);
  max-height: 92vh; display: flex; flex-direction: column; max-width: 96vw;
}
.modal-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 14px 18px; border-bottom: 1px solid var(--border);
}
.modal-head h3 { margin: 0; font-size: 15px; }
.modal-body { padding: 16px 18px; overflow: auto; }
.fade-enter-active, .fade-leave-active { transition: opacity .18s; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
</style>
