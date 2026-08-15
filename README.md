# RAG Chatbot Tuyen Sinh Dai Hoc

Chatbot tu van tuyen sinh su dung RAG (Retrieval-Augmented Generation) voi NVIDIA Nemotron qua OpenRouter, FAISS vector search, va Streamlit UI.

---

## Cau truc du an

```
rag_tuyensinh/
├── config.py             # [Cell 0]  Toan bo cau hinh he thong
├── doc_loader.py         # [Cell 1]  Load file PDF/TXT tu thu muc data/
├── doc_parser.py         # [Cell 2]  Parse layout PDF/TXT thanh text sach
├── chunker.py            # [Cell 3]  Child/parent chunking + metadata
├── indexer.py            # [Cell 4]  Embed + FAISS index + BM25 index
├── retriever.py          # [Cell 5]  Hybrid retrieval + reranker + parent fetch
├── memory.py             # [Cell 6]  Short-term / profile / episodic memory
├── prompt_templates.py   # [Cell 7]  Prompt templates chuan (RAG + citation)
├── llm_chain.py          # [Cell 8]  LLM setup qua OpenRouter + LangChain
├── response_builder.py   # [Cell 9]  Format response + citation + warning
├── chatbot.py            # [Cell 10] Orchestrator ket noi toan bo pipeline
├── app.py                # [Cell 11] Streamlit UI
├── requirements.txt
└── data/                 # Dat cac file PDF/TXT tuyen sinh vao day
```

---

## Cai dat

```bash
# 1. Tao virtual environment
python -m venv venv
source venv/bin/activate      # Linux/Mac
venv\Scripts\activate         # Windows

# 2. Cai dependencies
pip install -r requirements.txt

# 3. Tao thu muc data va them tai lieu
mkdir data
# Sao chep cac file PDF/TXT tuyen sinh vao thu muc data/
```

---

## Chay ung dung

```bash
# Chay Streamlit UI
streamlit run app.py
```

Lan dau chay, he thong se tu dong:
1. Load tat ca file trong `data/`
2. Parse noi dung (PDF layout-aware, TXT encoding-safe)
3. Chia chunk (child + parent voi metadata day du)
4. Embed va tao FAISS index + BM25 index
5. Luu index vao thu muc `index/`

Tu lan 2, index se duoc load truc tiep (khong can build lai).

---

## Quy uoc dat ten file (de tu dong trich xuat metadata)

```
{nam}_{ky}_{campus}_{loai}.pdf
```

Vi du:
- `2024_HK1_HN_de_an_tuyen_sinh.pdf` → nam=2024, campus=Ha Noi
- `2025_HCM_faq_tuyen_sinh.txt` → nam=2025, campus=TP.HCM, loai=FAQ

Neu khong theo quy uoc, van hoat dong binh thuong nhung metadata se thieu chi tiet.

---

## Tinh nang chinh

- **Hybrid retrieval**: Ket hop FAISS (dense semantic) + BM25 (keyword) voi RRF fusion
- **Reranker**: Cross-encoder rerank top-k truoc khi tra loi
- **Parent-child chunking**: Retrieve child chunk chinh xac, but cung cap parent chunk day du cho LLM
- **Citation**: Moi tra loi deu kem nguon ro rang (file, trang, ky)
- **Memory**: Profile nguoi dung duoc tu dong cap nhat, lich su hoi thoai trong session
- **Confidence warning**: Canh bao khi thong tin co do tin cay thap
- **Streaming**: Hien thi real-time tren Streamlit

---

## Debug tung module

```bash
python doc_loader.py      # Kiem tra load file
python doc_parser.py      # Kiem tra parse noi dung
python chunker.py         # Kiem tra chunking
python indexer.py         # Build + kiem tra index
python retriever.py       # Kiem tra retrieval
python memory.py          # Kiem tra memory
python prompt_templates.py # Kiem tra prompt
python llm_chain.py       # Kiem tra ket noi LLM
python response_builder.py # Kiem tra format response
python chatbot.py         # Chay CLI chat khong co UI
```
