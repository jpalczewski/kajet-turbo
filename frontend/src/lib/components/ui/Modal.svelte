<script lang="ts">
  import type { Snippet } from 'svelte';
  import IconButton from './IconButton.svelte';

  let {
    title,
    children,
    actions,
    onclose,
  }: {
    title: string;
    children: Snippet;
    actions?: Snippet;
    onclose?: () => void;
  } = $props();

  let dialog: HTMLDialogElement;

  export function show() {
    dialog.showModal();
  }
  export function close() {
    dialog.close();
  }
</script>

<dialog
  bind:this={dialog}
  class="modal"
  onclick={(e) => e.target === dialog && dialog.close()}
  {onclose}
>
  <div class="modal__content">
    <header class="modal__header">
      <span class="modal__handle"></span>
      <h2 class="modal__title">{title}</h2>
      <IconButton label="Zamknij" onclick={() => dialog.close()}>×</IconButton>
    </header>
    <div class="modal__body">
      {@render children()}
    </div>
    {#if actions}
      <div class="modal__actions">
        {@render actions()}
      </div>
    {/if}
  </div>
</dialog>

<style lang="scss">
  @use '$lib/styles/variables' as v;
  @use '$lib/styles/breakpoints' as bp;

  .modal {
    width: min(420px, calc(100vw - 32px));
    padding: 0;
    border: 1px solid v.$border;
    border-radius: v.$radius-lg;
    background: v.$bg-raised;
    color: v.$text-primary;

    &::backdrop {
      background: rgba(0, 0, 0, 0.72);
    }
  }

  .modal__content {
    display: flex;
    flex-direction: column;
    gap: v.$space-lg;
    padding: v.$space-lg;
  }

  .modal__header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: v.$space-md;
  }

  .modal__title {
    margin: 0;
    font-family: v.$font-mono;
    font-size: 1rem;
  }

  .modal__body {
    display: flex;
    flex-direction: column;
    gap: v.$space-lg;
  }

  .modal__handle {
    display: none;
  }

  .modal__actions {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: v.$space-sm;
  }

  @include bp.mobile {
    // Native modal <dialog> is centered via `inset:0; margin:auto`; re-pin to bottom as a sheet.
    .modal {
      width: 100%;
      max-width: 100%;
      margin: 0;
      inset: auto 0 0 0;
      border: none;
      border-top: 1px solid v.$border-accent;
      border-radius: v.$radius-lg v.$radius-lg 0 0;
    }

    .modal__content {
      padding-bottom: calc(#{v.$space-lg} + env(safe-area-inset-bottom));
    }

    .modal__header {
      position: relative;
      padding-top: v.$space-sm;
    }

    .modal__handle {
      display: block;
      position: absolute;
      top: 0;
      left: 50%;
      transform: translateX(-50%);
      width: 32px;
      height: 4px;
      border-radius: 2px;
      background: v.$text-muted;
    }
  }
</style>
