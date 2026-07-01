<script lang="ts">
	import { models } from '$lib/stores';
	import Tooltip from '../common/Tooltip.svelte';

	// Two-way bound from Navbar → same array of model ids the dropdown uses.
	export let selectedModels = [''];

	const selectModel = (id: string) => {
		selectedModels = [id];
	};
</script>

{#if $models.length > 0}
	<div
		class="flex items-end gap-1.5 px-2 py-1.5 rounded-2xl bg-white/70 dark:bg-gray-850/70 border border-gray-100 dark:border-gray-800 shadow-sm backdrop-blur-sm"
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
						class="size-9 rounded-full object-cover ring-2 transition-all duration-150 {active
							? 'ring-gray-800 dark:ring-gray-100'
							: 'ring-transparent'}"
					/>
					{#if active}
						<span
							class="absolute -bottom-1 left-1/2 -translate-x-1/2 size-1 rounded-full bg-gray-800 dark:bg-gray-100"
						></span>
					{/if}
				</button>
			</Tooltip>
		{/each}
	</div>
{/if}
