<script setup lang="ts">defineProps<{ status: string }>()</script>
<template><span class="badge" :class="status.toLowerCase()"><i aria-hidden="true"></i>{{ status }}</span></template>
