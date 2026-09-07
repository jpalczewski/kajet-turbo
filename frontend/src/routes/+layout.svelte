<script lang="ts">
  import '@fontsource-variable/jetbrains-mono';
  import { page } from '$app/state';
  import Navbar from '$lib/components/Navbar.svelte';
  import '$lib/styles/global.scss';

  let { children } = $props();

  const CHROMELESS_ROUTES = ['/login', '/shared/[token]'];
  const showNavbar = $derived(!CHROMELESS_ROUTES.includes(page.route.id ?? ''));
</script>

{#if showNavbar}
  <Navbar />
{/if}

{@render children()}
