// Retrieved sources — the chunks the rag-backend actually fed to the LLM for one
// answer, fetched once the answer has finished streaming and then stored with the
// chat as its citations.
//
// OWUI's own citation machinery is switched off for MINTIFY (utils/middleware.py
// skips chat_completion_files_handler for Pipelines models): under
// BYPASS_EMBEDDING_AND_RETRIEVAL it produced each attached file's ENTIRE extracted
// text, which was neither what the model saw nor worth persisting per message.
//
// URL is environment-aware via $lib/utils/ragApi — never hardcode a port.

import { ragApiBase } from '$lib/utils/ragApi';

type RetrievedChunk = {
	source: string;
	text: string;
	page: number | null;
	file_id: string | null;
	doc_type: string | null;
};

// The shape Citations.svelte expects: one entry per document, chunks in parallel
// `document` / `metadata` arrays. `metadata[i].source` is what the UI groups and
// labels citations by.
export type MessageSource = {
	source: { name: string };
	document: string[];
	metadata: { source: string; name: string; file_id?: string; page?: number }[];
};

// Deliberately no `distances`: our scores are RRF rank scores (~0.01–0.5), not
// similarities, and Citations.svelte would render them as a relevance percentage.
const toMessageSources = (chunks: RetrievedChunk[]): MessageSource[] => {
	const byDocument = new Map<string, MessageSource>();

	for (const chunk of chunks) {
		const name = chunk.source || 'unbekannt';
		let entry = byDocument.get(name);
		if (!entry) {
			entry = { source: { name }, document: [], metadata: [] };
			byDocument.set(name, entry);
		}

		entry.document.push(chunk.text);
		entry.metadata.push({
			source: name,
			name,
			...(chunk.file_id ? { file_id: chunk.file_id } : {}),
			...(chunk.page !== null && chunk.page !== undefined ? { page: chunk.page } : {})
		});
	}

	return [...byDocument.values()];
};

// Returns [] for an answer that used no documents (model-only turn, or a cache
// miss after a backend restart) and on any failure — citations are a nice-to-have,
// never a reason to interrupt the chat.
export const getRetrievedSources = async (
	token: string,
	messageId: string
): Promise<MessageSource[]> => {
	try {
		const res = await fetch(`${ragApiBase()}/api/retrieved-sources/${messageId}`, {
			headers: { Authorization: `Bearer ${token}` }
		});
		if (!res.ok) {
			return [];
		}
		const chunks: RetrievedChunk[] = (await res.json())?.sources ?? [];
		return toMessageSources(chunks);
	} catch (err) {
		console.error('retrieved-sources:', err);
		return [];
	}
};
