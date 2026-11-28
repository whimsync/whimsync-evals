---
description: Academic style page: title, authors, abstract, numbered sections.
---
# Adaptive Chunking for Long Context Retrieval

Meera Krishnan, Daniel Okafor and Lena Hoffmann

## Abstract

Retrieval augmented generation systems typically split documents into fixed size chunks before embedding. We show that fixed chunking fragments tables and multi sentence arguments, reducing answer accuracy by up to nineteen percent on long technical documents. We propose an adaptive chunker that follows structural boundaries detected from layout and syntax. On three public benchmarks the adaptive chunker improves exact match accuracy by six to eleven points while producing thirty percent fewer chunks.

## 1 Introduction

Large language models can only attend to a bounded context, so external retrieval remains essential for question answering over large corpora. The quality of retrieval depends heavily on how source documents are segmented.

## 2 Method

Our chunker first parses document structure into a tree of sections, tables, lists and paragraphs. Leaves are merged greedily until a token budget is reached, never crossing a section boundary.

## 3 Results

Adaptive chunking outperformed fixed chunking on every benchmark, with the largest gains on documents that contain many tables.
