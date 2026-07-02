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
		class="flex items-center rounded-full border border-gray-100 dark:border-gray-800 bg-white dark:bg-gray-850 shadow-sm transition-all duration-200
		{home ? 'gap-2.5 px-2.5 py-2 mt-6' : 'gap-1.5 px-1.5 py-1'}"
	>
		{#each $models as model (model.id)}
			{@const active = selectedModels[0] === model.id}
			<Tooltip content={model.name} placement="bottom">
				<button
					type="button"
					aria-label={model.name}
					aria-pressed={active}
					on:click={() => selectModel(model.id)}
					class="flex items-center justify-center rounded-full transition-all duration-150 ease-out focus:outline-none {active
						? 'scale-110'
						: 'opacity-70 hover:opacity-100 hover:scale-105'}"
				>
					<img
						src={model?.info?.meta?.profile_image_url ?? '/static/favicon.png'}
						alt={model.name}
						class="rounded-full object-cover bg-white dark:bg-gray-900 transition-all duration-150 {home
							? 'size-12'
							: 'size-9'} {active
							? 'ring-[3px] ring-mintify-green'
							: 'ring-2 ring-gray-200 dark:ring-gray-700'}"
					/>
				</button>
			</Tooltip>
		{/each}
	</div>
{/if}
