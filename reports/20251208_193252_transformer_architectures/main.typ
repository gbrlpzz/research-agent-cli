#import "lib.typ": project

#show: project.with(
  title: "Transformer Architectures: Evolution, Efficiency, and Applications",
  subtitle: "A Comprehensive Review of State-of-the-Art Models",
  authors: ("Research Agent",),
  date: "October 2023",
  abstract: [
    The Transformer architecture has fundamentally revolutionized deep learning, displacing Recurrent Neural Networks (RNNs) as the dominant paradigm for sequence modeling. Since its introduction in "Attention Is All You Need", the architecture has diverged into distinct families—Encoder-only (BERT), Decoder-only (GPT), and Encoder-Decoder models—each optimized for specific objectives. This report synthesizes the core mechanisms of the Transformer, explores its primary architectural variants, and analyzes critical advancements in efficiency such as FlashAttention and Mixture-of-Experts (MoE). Furthermore, it examines the generalization of the architecture beyond text to computer vision via the Vision Transformer (ViT).
  ]
)

#outline(indent: auto)
#pagebreak()

= Introduction
The introduction of the Transformer model by Vaswani et al. in 2017 marked a paradigm shift in natural language processing (NLP) @Attention_Is_Al_Vaswan_2017. Prior to this, sequence modeling relied heavily on Recurrent Neural Networks (RNNs) and Long Short-Term Memory (LSTM) networks, which processed data sequentially and precluded parallelization. The Transformer discarded recurrence entirely, relying instead on a mechanism called "self-attention" to draw global dependencies between input and output.

This architecture has since become the foundation for the most advanced models in artificial intelligence, scaling to billions of parameters and demonstrating few-shot learning capabilities that approach human performance in specific domains @Language_Models_Brown_2020. This report details the evolution of the Transformer, from its core components to modern efficient variants and cross-modal applications.

= Core Architecture
The original Transformer is an Encoder-Decoder structure, though many modern variants utilize only one of these stacks. The architecture is defined by three primary components: Self-Attention, Multi-Head Attention, and Positional Encoding.

== Self-Attention Mechanism
At the heart of the Transformer is the self-attention mechanism, which relates different positions of a single sequence to compute a representation of the sequence. For a given input, the model generates three vectors: Query ($Q$), Key ($K$), and Value ($V$). The attention score is calculated as:

$ "Attention"(Q, K, V) = "softmax"((Q K^T) / sqrt(d_k)) V $

where $d_k$ is the dimension of the key vectors. The division by $sqrt(d_k)$ acts as a scaling factor to prevent vanishing gradients in the softmax function @Attention_Is_Al_Vaswan_2017. This mechanism allows every token to attend to every other token in the sequence simultaneously, enabling the modeling of long-range dependencies that were challenging for RNNs.

== Multi-Head Attention
To capture different types of relationships (e.g., syntactic vs. semantic), the Transformer employs Multi-Head Attention. This involves running the self-attention mechanism in parallel $h$ times with different, learned linear projections. The outputs are concatenated and projected once more, allowing the model to jointly attend to information from different representation subspaces @Attention_Is_Al_Vaswan_2017.

== Positional Encoding
Since the Transformer contains no recurrence and no convolution, it requires an explicit signal regarding the order of the sequence. Positional encodings are added to the input embeddings to provide this information. The original implementation used sine and cosine functions of different frequencies, though learnable embeddings are also common in later architectures @Efficient_Trans_Tay_Y_2020.

= Architectural Paradigms
Following the original Encoder-Decoder design, research diverged into specialized architectures tailored for specific tasks.

== Encoder-Only Models (BERT)
Encoder-only models, exemplified by BERT (Bidirectional Encoder Representations from Transformers), are designed to understand the full context of a sequence. BERT utilizes a "masked language model" (MLM) objective, where random tokens in the input are masked, and the model must predict the original token based on both left and right context @BERT_Pre_train_Devlin_2018.

This bidirectional nature makes encoder-only models superior for understanding tasks such as text classification, named entity recognition, and question answering. However, because they "see" the future tokens during training, they are ill-suited for open-ended text generation.

== Decoder-Only Models (GPT)
Decoder-only models, such as the GPT (Generative Pre-trained Transformer) series, focus on generative tasks. These models employ a causal masking scheme (or "masked self-attention") that prevents positions from attending to subsequent positions. This enforces a unidirectional (left-to-right) flow of information @Language_Models_Brown_2020.

GPT-3 demonstrated that scaling these autoregressive models to 175 billion parameters unlocks "few-shot" learning abilities, where the model can perform novel tasks given only a natural language prompt and a few examples, without gradient updates @Language_Models_Brown_2020. While less efficient at capturing bidirectional context than BERT, their generative capabilities make them the standard for large language models (LLMs).

= Efficiency and Scaling
A major bottleneck of the standard Transformer is the quadratic complexity $O(N^2)$ of the self-attention mechanism with respect to sequence length $N$. This limits the processing of long documents. Several innovations address this limitation.

== FlashAttention
FlashAttention addresses the memory bandwidth bottleneck rather than just operation count. Standard attention implementations require repeatedly reading and writing large matrices to High Bandwidth Memory (HBM). FlashAttention uses tiling to compute attention blocks entirely in the faster on-chip SRAM, significantly reducing memory access overhead @FlashAttention_Dao_T_2022. This "IO-aware" approach yields speedups of 2-4x and allows for training with significantly longer sequence lengths without approximation.

== Mixture of Experts (MoE)
To scale model capacity without a proportional increase in computational cost, architectures like Switch Transformers employ Mixture of Experts (MoE). In these models, the dense feed-forward network (FFN) layers are replaced by a sparse layer containing multiple "experts". For each token, a routing mechanism selects only a subset of experts (e.g., top-1) to process the input @Switch_Transfor_Fedus_2021. This decouples parameter count from floating-point operations (FLOPs), enabling the training of trillion-parameter models that remain efficient during inference.

= Transformers in Vision
The success of Transformers in NLP prompted their application to computer vision. The Vision Transformer (ViT) applies the pure transformer architecture directly to sequences of image patches @An_Image_is_Wor_Dosovi_2020.

ViT splits an image into fixed-size patches (e.g., 16x16 pixels), linearly embeds each patch, adds positional embeddings, and feeds the resulting sequence of vectors into a standard Transformer encoder. Unlike Convolutional Neural Networks (CNNs), which have inductive biases for translation invariance and locality baked in, ViT learns these relationships from data. Consequently, ViT requires larger datasets (like JFT-300M) to outperform ResNet baselines but achieves state-of-the-art performance at scale @An_Image_is_Wor_Dosovi_2020.

= Conclusion
The Transformer architecture has proven to be a remarkably generalizable framework for deep learning. From the bidirectional understanding of BERT to the generative power of GPT-3, and efficient scaling via MoE and FlashAttention, the ecosystem continues to evolve rapidly. The successful translation of the architecture to computer vision further underscores its versatility. Future research will likely focus on further reducing the quadratic cost of attention and enhancing the reasoning capabilities of these models beyond statistical pattern matching @Efficient_Trans_Tay_Y_2020.

#bibliography("refs.bib")