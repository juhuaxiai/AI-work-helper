<script setup lang="ts">
import type { AlertNotification } from "@agent-py/api-contracts";

defineProps<{ readonly items: readonly AlertNotification[] }>();
const emit = defineEmits<{ select: [diagnosticId: string] }>();

const formatTime = new Intl.DateTimeFormat("zh-CN", {
  month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit"
});
</script>

<template>
  <details id="alert-deliveries" class="alert-notification-history" :open="items.length > 0">
    <summary>告警通知</summary>
    <p v-if="items.length === 0">暂无推送告警</p>
    <ul v-else>
      <li v-for="item in items" :key="item.id">
        <strong>{{ item.alertName || item.source }}</strong>
        <small>{{ item.service }} · {{ item.environment }} · {{ item.status === 'firing' ? '触发' : '已解除' }}</small>
        <time :datetime="item.startsAt">{{ formatTime.format(new Date(item.startsAt)) }}</time>
        <button v-if="item.diagnosticId" type="button" @click="emit('select', item.diagnosticId)">查看诊断</button>
      </li>
    </ul>
  </details>
</template>

<style scoped>
.alert-notification-history { border: 1px solid var(--line); border-radius: var(--radius-sm); padding: .7rem .8rem; }
summary { cursor: pointer; font-weight: 650; }
p, small, time { color: var(--text-secondary); font-size: .78rem; }
ul { display: grid; gap: .45rem; list-style: none; margin: .6rem 0 0; padding: 0; }
li { border-top: 1px solid var(--line); display: grid; gap: .2rem; overflow-wrap: anywhere; padding-top: .5rem; }
button { color: var(--accent-strong); justify-self: start; padding: .25rem 0; }
</style>
