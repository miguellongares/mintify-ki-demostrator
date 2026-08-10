<script context="module" lang="ts">
	// Outer height of a dock pill in each mode. The navbar's other buttons
	// (new chat, Richter Modus, user menu) import these so the whole row stays
	// flush — if a pill's padding or avatar size changes, update these too.
	export const DOCK_HEIGHT = {
		home: 'h-[2.875rem]', // 32px avatar + 2×6px padding + 2×1px border
		chat: 'h-[2.125rem]' // 24px avatar + 2×4px padding + 2×1px border
	};

	// Same measurements as a square, for the icon-only buttons next to the dock.
	export const DOCK_SQUARE = {
		home: 'size-[2.875rem]',
		chat: 'size-[2.125rem]'
	};
</script>

<script lang="ts">
	import { models } from '$lib/stores';

	// Two-way bound from Navbar → same array of model ids the dropdown uses.
	export let selectedModels = [''];
	// On the landing/new-chat page we render the dock bigger.
	export let home = false;

	const selectModel = (id: string) => {
		selectedModels = [id];
	};
</script>

{#if $models.length > 0}
	<div
		class="flex items-center max-w-full overflow-x-auto scrollbar-hidden {home
			? 'gap-2'
			: 'gap-1.5'}"
	>
		{#each $models as model (model.id)}
			{@const active = selectedModels[0] === model.id}
			<button
				type="button"
				aria-pressed={active}
				on:click={() => selectModel(model.id)}
				class="flex shrink-0 items-center rounded-full border bg-white dark:bg-gray-850 shadow-sm transition-all duration-150 ease-out focus:outline-none whitespace-nowrap
					{home ? DOCK_HEIGHT.home : DOCK_HEIGHT.chat}
					{home ? 'gap-2 pl-1.5 pr-3.5 py-1.5' : 'gap-1.5 pl-1 pr-2.5 py-1'}
					{active
					? 'border-mintify-green ring-1 ring-mintify-green'
					: 'border-gray-200 dark:border-gray-700 opacity-70 hover:opacity-100'}"
			>
				<img
					src={model?.info?.meta?.profile_image_url ?? '/static/favicon.png'}
					alt=""
					class="rounded-full object-cover bg-white dark:bg-gray-900 {home ? 'size-8' : 'size-6'}"
				/>
				<span class="font-medium text-gray-800 dark:text-gray-100 {home ? 'text-sm' : 'text-xs'}">
					{model.name}
				</span>
			</button>
		{/each}
	</div>
{/if}
