<script lang="ts">
  import type { Snippet } from 'svelte';
  import { useAsyncAction } from '$lib/utils/async-action.svelte';
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
  const action = useAsyncAction();

  async function handleConfirm() {
    await action.run(async () => {
      await onconfirm();
      modal.close();
    });
  }
</script>

{@render trigger({ open: () => modal.show() })}

<Modal
  bind:this={modal}
  {title}
  onclose={() => {
    action.clearError();
  }}
>
  <p class="message">{message}</p>
  {#if action.error}
    <p class="error">{action.error}</p>
  {/if}
  {#snippet actions()}
    <button class="btn btn--secondary" onclick={() => modal.close()} disabled={action.busy}>
      Anuluj
    </button>
    <button class="btn btn--{confirmVariant}" onclick={handleConfirm} disabled={action.busy}>
      {action.busy ? '…' : confirmLabel}
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
