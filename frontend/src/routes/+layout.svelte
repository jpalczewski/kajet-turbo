<script lang="ts">
  import '@fontsource-variable/jetbrains-mono';
  import { page } from '$app/state';
  import Navbar from '$lib/components/Navbar.svelte';
  import '$lib/styles/global.scss';

  let { children } = $props();

  const showNavbar = $derived((page.route.id as string | null) !== '/login');
</script>

{#if showNavbar}
  <Navbar />
{/if}

{@render children()}
