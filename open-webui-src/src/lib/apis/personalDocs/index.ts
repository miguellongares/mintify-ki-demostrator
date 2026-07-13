// Personal files (sidebar drag & drop) — stored in the rag-backend under
// data/personal_docs (SQLite + raw files), scoped per user via the OWUI token.
// URLs are environment-aware like the Bewertung panel: direct port in dev,
// /rag-api/* behind Caddy in production. Do NOT hardcode ports elsewhere.

export type PersonalDoc = {
	document_id: string;
	filename: string;
	content_type: string | null;
	size_bytes: number;
	created_at: string;
};

const getBaseUrl = () =>
	window.location.hostname === 'localhost'
		? 'http://localhost:8088/api/personal-docs'
		: `${window.location.origin}/rag-api/api/personal-docs`;

const authHeaders = (token: string) => ({ Authorization: `Bearer ${token}` });

export const listPersonalDocs = async (token: string): Promise<PersonalDoc[]> => {
	const res = await fetch(getBaseUrl(), { headers: authHeaders(token) });
	if (!res.ok) {
		throw new Error(`Dateien konnten nicht geladen werden (${res.status})`);
	}
	return (await res.json()).documents;
};

export const uploadPersonalDoc = async (token: string, file: File): Promise<PersonalDoc> => {
	const form = new FormData();
	form.append('file', file);
	const res = await fetch(getBaseUrl(), {
		method: 'POST',
		headers: authHeaders(token),
		body: form
	});
	if (!res.ok) {
		throw new Error(
			res.status === 413 ? 'Datei ist größer als 50 MB' : `Upload fehlgeschlagen (${res.status})`
		);
	}
	return res.json();
};

export const downloadPersonalDoc = async (token: string, doc: PersonalDoc): Promise<void> => {
	const res = await fetch(`${getBaseUrl()}/${doc.document_id}/download`, {
		headers: authHeaders(token)
	});
	if (!res.ok) {
		throw new Error(`Download fehlgeschlagen (${res.status})`);
	}
	const url = URL.createObjectURL(await res.blob());
	const anchor = document.createElement('a');
	anchor.href = url;
	anchor.download = doc.filename;
	document.body.appendChild(anchor);
	anchor.click();
	anchor.remove();
	URL.revokeObjectURL(url);
};

export const deletePersonalDoc = async (token: string, documentId: string): Promise<void> => {
	const res = await fetch(`${getBaseUrl()}/${documentId}`, {
		method: 'DELETE',
		headers: authHeaders(token)
	});
	if (!res.ok) {
		throw new Error(`Löschen fehlgeschlagen (${res.status})`);
	}
};
