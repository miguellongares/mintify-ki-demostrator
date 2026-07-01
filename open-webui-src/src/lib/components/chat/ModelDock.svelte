<script lang="ts">
	import { models } from '$lib/stores';
	import Tooltip from '../common/Tooltip.svelte';

	// Two-way bound from Navbar → same array of model ids the dropdown uses.
	export let selectedModels = [''];
	// On the landing/new-chat page we render the dock lower and bigger.
	export let home = false;

	const selectModel = (id: string) => {
		selectedModels = [id];
	};
</script>

{#if $models.length > 0}
	<div
		class="flex items-end rounded-2xl border shadow-sm backdrop-blur-sm transition-all duration-200
		bg-mintify-green/35 border-mintify-green-dark/40 dark:bg-mintify-green-dark/20 dark:border-mintify-green/25
		{home ? 'gap-2.5 px-3.5 py-2.5 mt-6' : 'gap-1.5 px-2 py-1.5'}"
	>
		{#each $models as model (model.id)}
			{@const active = selectedModels[0] === model.id}
			<Tooltip content={model.name} placement="bottom">
				<button
					type="button"
					aria-label={model.name}
					aria-pressed={active}
					on:click={() => selectModel(model.id)}
					class="group relative flex items-center justify-center rounded-full transition-all duration-150 ease-out origin-bottom hover:scale-110 focus:outline-none {active
						? 'scale-105'
						: 'opacity-60 hover:opacity-100'}"
				>
					<img
						src={model?.info?.meta?.profile_image_url ?? '/static/favicon.png'}
						alt={model.name}
						class="rounded-full object-cover ring-2 transition-all duration-150 {home
							? 'size-12'
							: 'size-9'} {active
							? 'ring-mintify-blue dark:ring-mintify-green'
							: 'ring-transparent'}"
					/>
					{#if active}
						<span
							class="absolute -bottom-1 left-1/2 -translate-x-1/2 size-1 rounded-full bg-mintify-blue dark:bg-mintify-green"
						></span>
					{/if}
				</button>
			</Tooltip>
		{/each}
	</div>
{/if}
