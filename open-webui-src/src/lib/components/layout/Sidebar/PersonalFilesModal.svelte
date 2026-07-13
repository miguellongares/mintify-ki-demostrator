<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import { toast } from 'svelte-sonner';

	import {
		deletePersonalDoc,
		downloadPersonalDoc,
		listPersonalDocs,
		uploadPersonalDoc,
		type PersonalDoc
	} from '$lib/apis/personalDocs';
	import Modal from '$lib/components/common/Modal.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import ArrowDownTray from '$lib/components/icons/ArrowDownTray.svelte';
	import Document from '$lib/components/icons/Document.svelte';
	import GarbageBin from '$lib/components/icons/GarbageBin.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';

	const dispatch = createEventDispatcher();

	export let show = false;

	let docs: PersonalDoc[] = [];
	let loading = false;
	let uploading = false;
	let fileInput: HTMLInputElement;

	const loadDocs = async () => {
		loading = true;
		try {
			docs = await listPersonalDocs(localStorage.token);
		} catch (error) {
			toast.error(error instanceof Error ? error.message : 'Dateien konnten nicht geladen werden');
			docs = [];
		}
		loading = false;
	};

	$: if (show) {
		loadDocs();
	}

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
		await loadDocs();
		dispatch('change');
	};

	const downloadDoc = async (doc: PersonalDoc) => {
		try {
			await downloadPersonalDoc(localStorage.token, doc);
		} catch (error) {
			toast.error(error instanceof Error ? error.message : 'Download fehlgeschlagen');
		}
	};

	const deleteDoc = async (doc: PersonalDoc) => {
		if (!confirm(`„${doc.filename}" wirklich löschen?`)) {
			return;
		}
		try {
			await deletePersonalDoc(localStorage.token, doc.document_id);
			toast.success(`„${doc.filename}" gelöscht`);
		} catch (error) {
			toast.error(error instanceof Error ? error.message : 'Löschen fehlgeschlagen');
		}
		await loadDocs();
		dispatch('change');
	};

	const formatSize = (bytes: number) => {
		if (bytes < 1024) return `${bytes} B`;
		if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
		return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
	};

	const formatDate = (iso: string) =>
		new Date(iso).toLocaleString('de-DE', { dateStyle: 'medium', timeStyle: 'short' });
</script>

<Modal bind:show size="md">
	<div class="font-primary text-gray-700 dark:text-gray-100">
		<div class="flex justify-between items-center px-5 pt-4 pb-1">
			<div class="text-lg font-medium">Persönliche Dateien</div>
			<button
				class="self-center text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 transition"
				aria-label="Schließen"
				on:click={() => {
					show = false;
				}}
			>
				<XMark className="size-5" />
			</button>
		</div>

		<div class="px-5 pb-5">
			<div class="flex items-center justify-between gap-2 mb-3">
				<div class="text-xs text-gray-500 dark:text-gray-400">
					Nur für dich sichtbar · max. 50 MB pro Datei
				</div>
				<input
					bind:this={fileInput}
					type="file"
					multiple
					hidden
					on:change={() => {
						const files = Array.from(fileInput.files ?? []);
						fileInput.value = '';
						if (files.length > 0) {
							uploadFiles(files);
						}
					}}
				/>
				<button
					class="flex items-center gap-1.5 rounded-full bg-mintify-blue text-white hover:bg-mintify-blue-light dark:bg-mintify-green dark:text-mintify-blue dark:hover:bg-mintify-green-dark transition px-3.5 py-1.5 text-sm font-medium disabled:opacity-50"
					disabled={uploading}
					on:click={() => fileInput.click()}
				>
					{#if uploading}
						<Spinner className="size-4" />
					{:else}
						+
					{/if}
					Hochladen
				</button>
			</div>

			{#if loading}
				<div
					class="flex justify-center items-center gap-2 py-8 text-sm text-gray-500 animate-pulse"
				>
					<Spinner className="size-4" />
					Lade Dateien…
				</div>
			{:else if docs.length === 0}
				<div class="py-8 text-center text-sm text-gray-500 dark:text-gray-400">
					Noch keine Dateien — ziehe Dateien auf „Persönliche Dateien" in der Seitenleiste oder
					nutze „Hochladen".
				</div>
			{:else}
				<div class="max-h-[24rem] overflow-y-auto scrollbar-hidden flex flex-col gap-1">
					{#each docs as doc (doc.document_id)}
						<div
							class="flex items-center gap-3 rounded-xl px-3 py-2 hover:bg-gray-50 dark:hover:bg-gray-850 transition"
						>
							<div class="shrink-0 text-gray-500 dark:text-gray-400">
								<Document className="size-5" />
							</div>
							<div class="flex-1 min-w-0">
								<div class="text-sm font-medium truncate" title={doc.filename}>
									{doc.filename}
								</div>
								<div class="text-xs text-gray-500 dark:text-gray-400">
									{formatSize(doc.size_bytes)} · {formatDate(doc.created_at)}
								</div>
							</div>
							<button
								class="shrink-0 p-1.5 rounded-lg text-gray-500 hover:text-gray-800 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-800 transition"
								aria-label="Herunterladen"
								title="Herunterladen"
								on:click={() => downloadDoc(doc)}
							>
								<ArrowDownTray className="size-4" />
							</button>
							<button
								class="shrink-0 p-1.5 rounded-lg text-gray-500 hover:text-red-600 dark:hover:text-red-400 hover:bg-gray-100 dark:hover:bg-gray-800 transition"
								aria-label="Löschen"
								title="Löschen"
								on:click={() => deleteDoc(doc)}
							>
								<GarbageBin className="size-4" />
							</button>
						</div>
					{/each}
				</div>
			{/if}
		</div>
	</div>
</Modal>
