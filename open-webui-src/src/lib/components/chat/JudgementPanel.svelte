<script lang="ts">
	import { showJudgement } from '$lib/stores';
	import { ragApiBase } from '$lib/utils/ragApi';
	import ChevronRight from '../icons/ChevronRight.svelte';

	type JudgeResult = { rating: string; reasoning: string };
	let judgeResults: JudgeResult[] | null = null;
	let judgeLoading = false;

	export const requestJudge = async (query: string, answer: string, chatId?: string) => {
		judgeLoading = true;
		judgeResults = null;
		try {
			const judgeUrl = `${ragApiBase()}/api/judge`;
			const resp = await fetch(judgeUrl, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ query, answer, chat_id: chatId })
			});
			if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
			const data = await resp.json();
			judgeResults = data.judges;
		} catch (e) {
			console.error('[JudgementPanel] judge fetch failed:', e);
			judgeResults = [
				{ rating: 'mittel', reasoning: 'Bewertung nicht verfügbar.' },
				{ rating: 'mittel', reasoning: 'Bewertung nicht verfügbar.' },
				{ rating: 'mittel', reasoning: 'Bewertung nicht verfügbar.' }
			];
		} finally {
			judgeLoading = false;
		}
	};

	function ratingColor(rating: string) {
		if (rating === 'gut') return 'border-green-500 bg-green-50 dark:bg-green-900/20 dark:border-green-500';
		if (rating === 'schlecht') return 'border-red-500 bg-red-50 dark:bg-red-900/20 dark:border-red-500';
		return 'border-yellow-400 bg-yellow-50 dark:bg-yellow-900/20 dark:border-yellow-400';
	}

	function ratingLabel(rating: string) {
		if (rating === 'gut') return 'Gut';
		if (rating === 'schlecht') return 'Schlecht';
		if (rating === 'mittel') return 'Mittel';
		return rating;
	}

	function ratingTextColor(rating: string) {
		if (rating === 'gut') return 'text-green-700 dark:text-green-400';
		if (rating === 'schlecht') return 'text-red-700 dark:text-red-400';
		return 'text-yellow-700 dark:text-yellow-400';
	}

	function ratingDotColor(rating: string) {
		if (rating === 'gut') return 'bg-green-500';
		if (rating === 'schlecht') return 'bg-red-500';
		return 'bg-yellow-400';
	}
</script>

<aside
	id="judgement-panel"
	class="h-screen max-h-[100dvh] min-h-[100dvh] select-none shrink-0 overflow-x-hidden fixed md:relative top-0 right-0 z-50 bg-mintify-tint text-gray-900 dark:bg-mintify-night dark:text-gray-200 text-sm transition-[width] duration-200 ease-in-out {$showJudgement
		? 'w-[350px] max-w-[350px] border-l border-mintify-blue/15 dark:border-mintify-blue/30'
		: 'w-0'}"
	data-state={$showJudgement}
>
	<div class="w-[350px] h-full px-4 py-4 overflow-y-auto scrollbar-hidden">
		<div class="flex flex-col gap-3">
			<div class="flex items-center justify-between mb-2">
				<h3 class="text-sm font-semibold text-mintify-blue dark:text-mintify-green">Bewertung</h3>
				<button
					class="cursor-pointer p-[7px] flex rounded-xl hover:bg-mintify-blue/10 dark:hover:bg-mintify-blue/25 transition"
					on:click={() => showJudgement.set(false)}
				>
					<ChevronRight className="size-5" />
				</button>
			</div>

			{#each [0, 1, 2] as i}
				{#if judgeLoading || judgeResults === null}
					<div class="border rounded-lg p-3 bg-gray-100 dark:bg-gray-800 border-gray-200 dark:border-gray-700">
						<div class="flex items-center gap-2.5">
							<div
								class="w-4 h-4 rounded-full border-2 border-mintify-blue/25 border-t-mintify-blue dark:border-mintify-green/25 dark:border-t-mintify-green {judgeLoading
									? 'animate-spin'
									: ''}"
							></div>
							<span class="text-xs font-medium text-gray-600 dark:text-gray-400">
								Richter {i + 1}{judgeLoading ? ' — denkt nach…' : ''}
							</span>
						</div>
					</div>
				{:else}
					<div class="border-l-4 rounded-lg p-3 {ratingColor(judgeResults[i].rating)}">
						<div class="flex items-center gap-2 mb-1">
							<div class="w-2 h-2 rounded-full {ratingDotColor(judgeResults[i].rating)}"></div>
							<span class="text-xs font-semibold {ratingTextColor(judgeResults[i].rating)}">
								Richter {i + 1} — {ratingLabel(judgeResults[i].rating)}
							</span>
						</div>
						<p class="text-xs text-gray-600 dark:text-gray-400 leading-relaxed">{judgeResults[i].reasoning}</p>
					</div>
				{/if}
			{/each}

			{#if !judgeLoading && judgeResults === null}
				<p class="text-xs text-gray-400 text-center pt-2">Sende eine Nachricht für eine Bewertung.</p>
			{/if}
		</div>
	</div>
</aside>
