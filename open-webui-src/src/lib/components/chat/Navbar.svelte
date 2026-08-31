<script lang="ts">
	import { getContext } from 'svelte';
	import { toast } from 'svelte-sonner';

	import {
		WEBUI_NAME,
		banners,
		chatId,
		config,
		mobile,
		settings,
		showArchivedChats,
		showJudgement,
		showSidebar,
		temporaryChatEnabled,
		user
	} from '$lib/stores';

	import { slide } from 'svelte/transition';
	import { page } from '$app/stores';

	import ShareChatModal from '../chat/ShareChatModal.svelte';
	import ModelSelector from '../chat/ModelSelector.svelte';
	import ModelDock, { DOCK_HEIGHT, DOCK_SQUARE } from '../chat/ModelDock.svelte';
	import Tooltip from '../common/Tooltip.svelte';
	import Menu from '$lib/components/layout/Navbar/Menu.svelte';
	import UserMenu from '$lib/components/layout/Sidebar/UserMenu.svelte';
	import MenuLines from '../icons/MenuLines.svelte';
	import PencilSquare from '../icons/PencilSquare.svelte';
	import Scale from '../icons/Scale.svelte';

	import Banner from '../common/Banner.svelte';

	const i18n = getContext('i18n');

	export let initNewChat: Function;
	export let title: string = $WEBUI_NAME;
	export let shareEnabled: boolean = false;

	export let chat;
	export let history;
	export let selectedModels;
	export let showModelSelector = true;

	let showShareChatModal = false;
	let showDownloadChatModal = false;

	// The model dock renders bigger on the landing screen than inside a chat.
	// The buttons to its right follow the same two heights so the row stays flush.
	$: isHome = !history?.currentId && !$chatId;
	$: navButtonHeight = isHome ? DOCK_HEIGHT.home : DOCK_HEIGHT.chat;
	$: navButtonSquare = isHome ? DOCK_SQUARE.home : DOCK_SQUARE.chat;
</script>

<ShareChatModal bind:show={showShareChatModal} chatId={$chatId} />

<nav class="sticky top-0 z-30 w-full py-1.5 -mb-8 flex flex-col items-center drag-region">
	<div class="flex items-center w-full px-1.5">
		<div
			class=" bg-linear-to-b via-50% from-white via-white to-transparent dark:from-gray-900 dark:via-gray-900 dark:to-transparent pointer-events-none absolute inset-0 -bottom-7 z-[-1]"
		></div>

		<div class=" flex max-w-full w-full mx-auto px-1 pt-0.5 bg-transparent">
			<div
				class="flex items-center w-full max-w-full md:grid md:grid-cols-[1fr_auto_1fr]"
			>
				<div
					class="{$showSidebar
						? 'md:hidden'
						: ''} mr-1 self-start flex flex-none items-center text-gray-600 dark:text-gray-400 md:col-start-1"
				>
					<button
						id="sidebar-toggle-button"
						class="cursor-pointer px-2 py-2 flex rounded-xl hover:bg-gray-50 dark:hover:bg-gray-850 transition"
						on:click={() => {
							showSidebar.set(!$showSidebar);
						}}
						aria-label="Toggle Sidebar"
					>
						<div class=" m-auto self-center">
							<MenuLines />
						</div>
					</button>
				</div>

				<div
					class="md:hidden flex-none overflow-hidden max-w-[45%] py-0.5
			{$showSidebar ? 'ml-1' : ''}
			"
				>
					{#if showModelSelector}
						<ModelSelector bind:selectedModels showSetDefault={!shareEnabled} />
					{/if}
				</div>

				{#if showModelSelector}
					<div class="hidden md:flex md:col-start-2 justify-center items-center min-w-0 px-2">
						<ModelDock bind:selectedModels home={isHome} />
					</div>
				{/if}

				<div
					class="self-start ml-auto md:ml-0 md:col-start-3 md:justify-self-end flex flex-none items-center text-gray-600 dark:text-gray-400"
				>
					<!-- <div class="md:hidden flex self-center w-[1px] h-5 mx-2 bg-gray-300 dark:bg-stone-700" /> -->
					{#if shareEnabled && chat && (chat.id || $temporaryChatEnabled)}
						<Menu
							{chat}
							{shareEnabled}
							shareHandler={() => {
								showShareChatModal = !showShareChatModal;
							}}
							downloadHandler={() => {
								showDownloadChatModal = !showDownloadChatModal;
							}}
						>
							<button
								class="flex cursor-pointer px-2 py-2 rounded-xl hover:bg-gray-50 dark:hover:bg-gray-850 transition"
								id="chat-context-menu-button"
							>
								<div class=" m-auto self-center">
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
											d="M6.75 12a.75.75 0 1 1-1.5 0 .75.75 0 0 1 1.5 0ZM12.75 12a.75.75 0 1 1-1.5 0 .75.75 0 0 1 1.5 0ZM18.75 12a.75.75 0 1 1-1.5 0 .75.75 0 0 1 1.5 0Z"
										/>
									</svg>
								</div>
							</button>
						</Menu>
					{/if}

					<!--
						Also the target of the sidebar's "Neuer Chat" entry, the model
						dropdown's temporary-chat toggle and Ctrl+Shift+O — they all
						reach it via document.getElementById('new-chat-button').click().
						Keep the id even if the button is hidden (md:hidden still
						dispatches programmatic clicks).
					-->
					<Tooltip content={$i18n.t('New Chat')}>
						<button
							id="new-chat-button"
							class="flex items-center justify-center {navButtonSquare} {$showSidebar
								? 'md:hidden'
								: ''} cursor-pointer mr-1 rounded-xl hover:bg-gray-50 dark:hover:bg-gray-850 transition-all duration-150 ease-out"
							on:click={() => {
								initNewChat();
							}}
							aria-label="New Chat"
						>
							<div class=" m-auto self-center">
								<PencilSquare className={isHome ? 'size-6' : 'size-5'} strokeWidth="2" />
							</div>
						</button>
					</Tooltip>

					<Tooltip content="Richter-Modus">
						<button
							class="flex items-center gap-2 cursor-pointer {navButtonHeight} {isHome
								? 'px-2 md:px-3.5'
								: 'px-2 md:px-3'} mr-1 rounded-full border font-medium transition-all duration-150 ease-out {$showJudgement
								? 'bg-mintify-green border-mintify-green-dark text-mintify-blue-dark hover:bg-mintify-green-dark'
								: 'bg-gray-100 dark:bg-gray-800 border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-700'}"
							on:click={async () => {
								await showJudgement.set(!$showJudgement);
							}}
							aria-label="Richter-Modus"
							aria-pressed={$showJudgement}
						>
							<Scale className="{isHome ? 'size-5' : 'size-4'} shrink-0" />
							<span
								class="hidden md:inline {isHome
									? 'text-sm'
									: 'text-xs'} whitespace-nowrap">Richter Modus</span
							>
						</button>
					</Tooltip>

					{#if $user !== undefined && $user !== null}
						<UserMenu
							className="max-w-[200px]"
							role={$user?.role}
							on:show={(e) => {
								if (e.detail === 'archived-chat') {
									showArchivedChats.set(true);
								}
							}}
						>
							<button
								class="select-none flex items-center justify-center {navButtonSquare} rounded-xl hover:bg-gray-50 dark:hover:bg-gray-850 transition-all duration-150 ease-out"
								aria-label="User Menu"
							>
								<div class=" self-center">
									<img
										src={$user?.profile_image_url}
										class="{isHome ? 'size-8' : 'size-6'} object-cover rounded-full"
										alt="User profile"
										draggable="false"
									/>
								</div>
							</button>
						</UserMenu>
					{/if}
				</div>
			</div>
		</div>
	</div>

	{#if !history.currentId && !$chatId && ($banners.length > 0 || ($config?.license_metadata?.type ?? null) === 'trial' || (($config?.license_metadata?.seats ?? null) !== null && $config?.user_count > $config?.license_metadata?.seats))}
		<div class=" w-full z-30 mt-5">
			<div class=" flex flex-col gap-1 w-full">
				{#if ($config?.license_metadata?.type ?? null) === 'trial'}
					<Banner
						banner={{
							type: 'info',
							title: 'Trial License',
							content: $i18n.t(
								'You are currently using a trial license. Please contact support to upgrade your license.'
							)
						}}
					/>
				{/if}

				{#if ($config?.license_metadata?.seats ?? null) !== null && $config?.user_count > $config?.license_metadata?.seats}
					<Banner
						banner={{
							type: 'error',
							title: 'License Error',
							content: $i18n.t(
								'Exceeded the number of seats in your license. Please contact support to increase the number of seats.'
							)
						}}
					/>
				{/if}

				{#each $banners.filter( (b) => (b.dismissible ? !JSON.parse(localStorage.getItem('dismissedBannerIds') ?? '[]').includes(b.id) : true) ) as banner}
					<Banner
						{banner}
						on:dismiss={(e) => {
							const bannerId = e.detail;

							localStorage.setItem(
								'dismissedBannerIds',
								JSON.stringify(
									[
										bannerId,
										...JSON.parse(localStorage.getItem('dismissedBannerIds') ?? '[]')
									].filter((id) => $banners.find((b) => b.id === id))
								)
							);
						}}
					/>
				{/each}
			</div>
		</div>
	{/if}
</nav>
