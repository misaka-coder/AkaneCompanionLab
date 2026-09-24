<script setup lang="ts">
withDefaults(defineProps<{ name: string; size?: number }>(), { size: 22 });
const paths: Record<string, string> = {
  home: "m3 10 9-7 9 7v10H3V10m6 10v-7h6v7",
  clothes: "m8 3-5 3-2 5 5 2v8h12v-8l5-2-2-5-5-3c0 5-8 5-8 0Z",
  bag: "M5 7h14l2 14H3L5 7Zm3 0V5a4 4 0 0 1 8 0v2",
  landscape: "M3 4h18v16H3V4Zm0 13 6-6 5 5 3-3 4 4M16 8h.01",
  music: "M9 18V5l11-2v13M9 5v5l11-2M9 18c0 4-7 4-7 0s7-4 7 0Zm11-2c0 4-7 4-7 0s7-4 7 0Z",
  settings: "M4 7h16M4 17h16M8 4v6m8 4v6",
  heart: "M12 21 3 12C-3 4 8-1 12 6c4-7 15-2 9 6l-9 9Z",
  sun: "M12 1v3m0 16v3M1 12h3m16 0h3M4 4l2 2m12 12 2 2M4 20l2-2M18 6l2-2M17 12a5 5 0 1 1-10 0 5 5 0 0 1 10 0Z",
  arrow: "M4 12h15m-5-5 5 5-5 5",
  close: "m6 6 12 12M6 18 18 6",
  upload: "M12 16V3m-5 5 5-5 5 5M3 15v6h18v-6",
  history: "M3 12a9 9 0 1 0 2-6M3 3v5h5m4-2v6l4 2",
  sound: "M3 9h4l5-5v16l-5-5H3V9Zm13-2c4 2 4 8 0 10m3-13c6 4 6 12 0 16",
  mute: "M3 9h4l5-5v16l-5-5H3V9Zm13 0 6 6m0-6-6 6",
  eye: "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Zm13 0a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z",
  leaf: "M20 3C6 0 0 11 7 17c6 6 16-3 13-14ZM4 21 16 8",
  tea: "M3 8h13v5a6 6 0 0 1-12 0L3 8Zm13 1h3a3 3 0 0 1 0 6h-3M2 21h18M7 2v3m5-3v3",
  book: "M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1-2.5-2.5Z M6.5 2v17.5",
  play: "m5 3 14 9-14 9V3z",
  check: "m5 12 5 5L20 7",
};
</script>
<template>
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.5"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path :d="paths[name] || paths.leaf" />
  </svg>
</template>
