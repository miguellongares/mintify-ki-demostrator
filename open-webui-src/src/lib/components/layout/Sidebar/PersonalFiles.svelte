<script lang="ts">
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';

	import { listPersonalDocs, uploadPersonalDoc } from '$lib/apis/personalDocs';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import PersonalFilesModal from './PersonalFilesModal.svelte';

	let showModal = false;
	let uploading = false;
	let docCount: number | null = null;
	// dragenter/dragleave also fire for child elements — a plain boolean flickers.
	let dragDepth = 0;

	const refreshCount = async () => {
		try {
			docCount = (await listPersonalDocs(localStorage.token)).length;
		} catch {
			docCount = null;
		}
	};

	const uploadFiles = async (files: File[]) => {
		uploading = true;
		for (const file of files) {
			try {
				await uploadPersonalDoc(localStorage.token, file);
				toast.success(`„${file.name}" gespeichert`);
			} catch (error) {
				toast.error(error instanceof Error ? error.message : 'Upload fehlgeschlagen');
			}
		}
		uploading = false;
		refreshCount();
	};

	onMount(refreshCount);
</script>

<!-- mt-3: the chat list above is a flex-1 scroll container and would otherwise
     end flush against this drop zone, with the last chat title clipped right at
     its border. The gap separates the two sections. -->
<div class="px-2 mt-3 mb-2 font-primary">
	<button
		class="w-full flex items-center gap-2.5 rounded-xl border border-dashed px-2.5 py-2 text-left transition
			{dragDepth > 0
			? 'border-mintify-green bg-mintify-green/10'
			: 'border-gray-300 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-850'}"
		aria-label="Persönliche Dateien öffnen"
		on:click={() => {
			showModal = true;
		}}
		on:dragenter|preventDefault|stopPropagation={() => {
			dragDepth += 1;
		}}
		on:dragover|preventDefault|stopPropagation={() => {}}
		on:dragleave|preventDefault|stopPropagation={() => {
			dragDepth = Math.max(0, dragDepth - 1);
		}}
		on:drop|preventDefault|stopPropagation={(e) => {
			dragDepth = 0;
			const files = Array.from(e.dataTransfer?.files ?? []);
			if (files.length > 0) {
				uploadFiles(files);
			}
		}}
	>
		<div class="shrink-0 text-gray-500 dark:text-gray-400">
			{#if uploading}
				<Spinner className="size-5" />
			{:else}
				<svg
					xmlns="http://www.w3.org/2000/svg"
					fill="none"
					viewBox="0 0 24 24"
					stroke-width="1.5"
					stroke="currentColor"
					class="size-5"
				>
					<path
						stroke-linecap="round"
						stroke-linejoin="round"
						d="M3.75 9.776c.112-.017.227-.026.344-.026h15.812c.117 0 .232.009.344.026m-16.5 0a2.25 2.25 0 0 0-1.883 2.542l.893 6.25a2.25 2.25 0 0 0 2.227 1.932h14.456a2.25 2.25 0 0 0 2.227-1.932l.893-6.25a2.25 2.25 0 0 0-1.883-2.542m-16.5 0V6A2.25 2.25 0 0 1 6 3.75h3.879a1.5 1.5 0 0 1 1.06.44l2.122 2.12a1.5 1.5 0 0 0 1.06.44H18A2.25 2.25 0 0 1 20.25 9v.776"
					/>
				</svg>
			{/if}
		</div>
		<div class="min-w-0">
			<div class="text-sm font-medium text-gray-800 dark:text-gray-100 truncate">
				Persönliche Dateien{docCount ? ` (${docCount})` : ''}
			</div>
			<div class="text-xs text-gray-500 dark:text-gray-400 truncate">
				{#if dragDepth > 0}
					Loslassen zum Hochladen
				{:else}
					<span class="md:hidden">Zum Hochladen tippen</span>
					<span class="hidden md:inline">Dateien hierher ziehen · Klick öffnet</span>
				{/if}
			</div>
		</div>
	</button>
</div>

<PersonalFilesModal bind:show={showModal} on:change={refreshCount} />
