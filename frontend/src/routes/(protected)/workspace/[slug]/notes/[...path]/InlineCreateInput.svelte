<script lang="ts">
  import { autofocus } from '$lib/actions/focus';

  let {
    placeholder,
    variant = 'list',
    validate,
    onsubmit,
    oncancel,
  }: {
    placeholder: string;
    variant?: 'tree' | 'list';
    validate?: (value: string) => string | null;
    onsubmit: (value: string) => Promise<void>;
    oncancel: () => void;
  } = $props();

  let value = $state('');
  let error = $state('');

  async function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') {
      oncancel();
      return;
    }
    if (e.key !== 'Enter') return;
    const trimmed = value.trim();
    if (!trimmed) return;
    const validationError = validate?.(trimmed);
    if (validationError) {
      error = validationError;
      return;
    }
    try {
      await onsubmit(trimmed);
    } catch (err: unknown) {
      error = err instanceof Error ? err.message : 'Błąd';
    }
  }
</script>

<input
  class="input input--{variant}"
  class:input--invalid={!!error}
  bind:value
  use:autofocus
  onkeydown={handleKeydown}
  {placeholder}
/>
{#if error}
  <span class="error">{error}</span>
{/if}

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .input {
    background: v.$bg-raised;
    color: v.$text-primary;
    font-family: v.$font-mono;
    font-size: 0.82rem;
    outline: none;
    border-radius: v.$radius-sm;
    box-sizing: border-box;

    &--tree {
      border: 1px solid v.$accent;
      padding: 1px 5px;
      width: 120px;
    }

    &--list {
      border: 1px solid v.$border;
      padding: 4px 8px;
      width: 100%;

      &:focus {
        border-color: v.$accent-dark;
      }
    }

    &--invalid {
      border-color: v.$error;
    }
  }

  .error {
    font-family: v.$font-mono;
    font-size: 0.72rem;
    color: v.$error;
  }
</style>
