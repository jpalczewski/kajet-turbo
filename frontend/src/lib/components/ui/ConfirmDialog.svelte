<script lang="ts">
  import type { Snippet } from 'svelte';
  import Modal from './Modal.svelte';

  let {
    title,
    message,
    confirmLabel,
    confirmVariant,
    onconfirm,
    trigger,
  }: {
    title: string;
    message: string;
    confirmLabel: string;
    confirmVariant: 'primary' | 'danger';
    onconfirm: () => Promise<void>;
    trigger: Snippet<[{ open: () => void }]>;
  } = $props();

  let modal: Modal;
  let loading = $state(false);
  let error = $state('');

  async function handleConfirm() {
    loading = true;
    error = '';
    try {
      await onconfirm();
      modal.close();
    } catch (e) {
      error = e instanceof Error ? e.message : 'Wystąpił błąd.';
    } finally {
      loading = false;
    }
  }
</script>

{@render trigger({ open: () => modal.show() })}

<Modal
  bind:this={modal}
  {title}
  onclose={() => {
    error = '';
  }}
>
  <p class="message">{message}</p>
  {#if error}
    <p class="error">{error}</p>
  {/if}
  {#snippet actions()}
    <button class="btn btn--secondary" onclick={() => modal.close()} disabled={loading}>
      Anuluj
    </button>
    <button class="btn btn--{confirmVariant}" onclick={handleConfirm} disabled={loading}>
      {loading ? '…' : confirmLabel}
    </button>
  {/snippet}
</Modal>

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .message {
    margin: 0;
    font-family: v.$font-mono;
    font-size: 0.85rem;
    color: v.$text-secondary;
  }

  .error {
    margin: 0;
    font-family: v.$font-mono;
    font-size: 0.8rem;
    color: v.$error;
  }
</style>
