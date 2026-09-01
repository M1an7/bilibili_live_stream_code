# Third-party notices

This runtime contains the following separately licensed components:

- Style-Bert-VITS2, pinned to the commit in `PINNED_STYLE_BERT_VITS2_COMMIT`, AGPL-3.0. Its source archive, README, LICENSE and LGPL notice are distributed with the runtime.
- aivmlib, pinned to the commit in `PINNED_AIVMLIB_COMMIT`, MIT. Its source archive, README and LICENSE are distributed with the runtime.
- `litagin/chinese-roberta-wwm-ext-large-onnx`, pinned to Hugging Face commit `d122490d3b1b03df20fefcc2d162e2be4fb6d3e6`, Apache-2.0. Only the fp16 ONNX model and tokenizer files required for Chinese inference are included.
- python-build-standalone CPython and Python packages installed from `requirements-windows.lock`; their license files and package metadata remain in the portable Python directory.

User voice models are external input and are never included in the runtime artifact.
