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
    confirmText,
  }: {
    title: string;
    message: string;
    confirmLabel: string;
    confirmVariant: 'primary' | 'danger';
    onconfirm: () => Promise<void>;
    trigger: Snippet<[{ open: () => void }]>;
    /** When set, the confirm button stays disabled until the user types this
     * value exactly — a GitHub-style safeguard for high-blast-radius actions. */
    confirmText?: string;
  } = $props();

  let modal: Modal;
  const action = useAsyncAction();
  let typedText = $state('');
  const confirmDisabled = $derived(
    action.busy || (confirmText !== undefined && typedText !== confirmText),
  );

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
    typedText = '';
  }}
>
  <p class="message">{message}</p>
  {#if confirmText !== undefined}
    <label class="confirm-text">
      <span class="confirm-text__label">Wpisz "{confirmText}", by potwierdzić:</span>
      <input
        type="text"
        bind:value={typedText}
        placeholder={confirmText}
        autocomplete="off"
        spellcheck="false"
        disabled={action.busy}
      />
    </label>
  {/if}
  {#if action.error}
    <p class="error">{action.error}</p>
  {/if}
  {#snippet actions()}
    <button class="btn btn--secondary" onclick={() => modal.close()} disabled={action.busy}>
      Anuluj
    </button>
    <button class="btn btn--{confirmVariant}" onclick={handleConfirm} disabled={confirmDisabled}>
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

  .confirm-text {
    display: flex;
    flex-direction: column;
    gap: v.$space-xs;

    &__label {
      font-family: v.$font-mono;
      font-size: 0.78rem;
      color: v.$text-secondary;
    }

    // _forms.scss already styles bare `input` (padding/background/border/font/transition);
    // only the deltas from that global rule live here.
    input {
      font-size: 0.9rem;

      &:focus {
        // global focus glow is still the pre-rebrand purple; override to the current accent
        box-shadow: 0 0 0 2px rgba(240, 184, 0, 0.12);
      }

      &::placeholder {
        color: v.$text-muted;
      }
      &:disabled {
        opacity: 0.5;
      }
    }
  }

  .error {
    margin: 0;
    font-family: v.$font-mono;
    font-size: 0.8rem;
    color: v.$error;
  }
</style>
