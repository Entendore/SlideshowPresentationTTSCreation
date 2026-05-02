#!/usr/bin/env python3
"""
Test script for the Text Chunker module.
Demonstrates the long text handling capabilities.
"""

from text_chunker import TextChunker, ChunkingConfig, get_chunker_for_language

def test_basic_chunking():
    """Test basic chunking functionality."""
    print("=" * 60)
    print("TEST 1: Basic Text Chunking")
    print("=" * 60)
    
    config = ChunkingConfig(max_chars=200, max_sentences=3)
    chunker = TextChunker(config)
    
    # Short text - should not need chunking
    short_text = "Hello world, this is a test."
    print(f"\nShort text ({len(short_text)} chars): '{short_text}'")
    print(f"  Needs chunking: {chunker.needs_chunking(short_text)}")
    
    # Long text - should be chunked
    long_text = """
    Artificial intelligence has revolutionized many industries. 
    From healthcare to finance, AI systems are making significant impacts. 
    Machine learning algorithms can now process vast amounts of data in seconds. 
    Natural language processing enables computers to understand human speech. 
    Computer vision allows machines to interpret visual information from the world. 
    These technologies are transforming how we live and work every day.
    """.strip()
    
    print(f"\nLong text ({len(long_text)} chars):")
    print(f"  Needs chunking: {chunker.needs_chunking(long_text)}")
    
    chunks = chunker.chunk_text(long_text, "English")
    print(f"  Split into {len(chunks)} chunks:")
    for i, (chunk, duration) in enumerate(chunks):
        print(f"\n  Chunk {i+1} (~{duration:.1f}s):")
        print(f"    '{chunk[:80]}...' " if len(chunk) > 80 else f"    '{chunk}'")


def test_multilingual():
    """Test chunking for different languages."""
    print("\n" + "=" * 60)
    print("TEST 2: Multilingual Support")
    print("=" * 60)
    
    # Chinese text
    chinese_text = "人工智能正在改变我们的生活方式。从智能手机到自动驾驶汽车，AI无处不在。机器学习算法变得越来越强大。自然语言处理让计算机能够理解人类的语言。"
    
    chunker = get_chunker_for_language("Chinese", max_chars=100)
    print(f"\nChinese text ({len(chinese_text)} chars):")
    chunks = chunker.chunk_text(chinese_text, "Chinese")
    print(f"  Split into {len(chunks)} chunks")
    for i, (chunk, _) in enumerate(chunks):
        print(f"  Chunk {i+1}: {chunk[:50]}..." if len(chunk) > 50 else f"  Chunk {i+1}: {chunk}")


def test_duration_estimation():
    """Test speech duration estimation."""
    print("\n" + "=" * 60)
    print("TEST 3: Speech Duration Estimation")
    print("=" * 60)
    
    chunker = TextChunker()
    
    texts = [
        ("English", "The quick brown fox jumps over the lazy dog. " * 5),
        ("Chinese", "人工智能正在改变我们的生活方式。" * 5),
        ("Japanese", "人工知能は私たちの生活を変えています。" * 5),
    ]
    
    for language, text in texts:
        duration = chunker.estimate_speech_duration(text, language)
        print(f"\n{language}:")
        print(f"  Text length: {len(text)} chars")
        print(f"  Estimated duration: {duration:.1f} seconds")


def test_edge_cases():
    """Test edge cases."""
    print("\n" + "=" * 60)
    print("TEST 4: Edge Cases")
    print("=" * 60)
    
    chunker = TextChunker(ChunkingConfig(max_chars=100))
    
    # Empty text
    print(f"\nEmpty text: {chunker.chunk_text('', 'English')}")
    
    # Very long single sentence (no punctuation)
    long_sentence = "This is a very long sentence without any punctuation that keeps going and going and going and should be hard split eventually because there are no natural break points in the text"
    print(f"\nLong sentence without breaks ({len(long_sentence)} chars):")
    chunks = chunker.chunk_text(long_sentence, "English")
    print(f"  Split into {len(chunks)} chunks")
    
    # Text with lots of punctuation
    punctuated = "Hello! How are you? I'm fine, thanks. What about you? Great! Let's go."
    print(f"\nPunctuated text ({len(punctuated)} chars):")
    chunks = chunker.chunk_text(punctuated, "English")
    print(f"  Split into {len(chunks)} chunks")


def test_very_long_text():
    """Test with a very long text (simulating real slide content)."""
    print("\n" + "=" * 60)
    print("TEST 5: Very Long Text (Real-world Scenario)")
    print("=" * 60)
    
    # Simulate a long slide narration
    long_narration = """
    Welcome to our comprehensive guide on machine learning fundamentals. 
    In this presentation, we will explore the core concepts that power modern artificial intelligence systems.
    
    Machine learning is a subset of artificial intelligence that enables computers to learn and improve from experience without being explicitly programmed. 
    The primary aim is to allow computers to learn automatically without human intervention or assistance and adjust actions accordingly.
    
    There are three main types of machine learning: supervised learning, unsupervised learning, and reinforcement learning. 
    Supervised learning uses labeled data to train algorithms to classify data or predict outcomes accurately. 
    Unsupervised learning analyzes and clusters unlabeled datasets to discover hidden patterns without human intervention. 
    Reinforcement learning is a behavioral machine learning model that is similar to how humans learn from trial and error.
    
    Key algorithms in machine learning include linear regression, decision trees, random forests, support vector machines, and neural networks. 
    Each algorithm has its strengths and is suited for different types of problems.
    
    Data preprocessing is a crucial step in any machine learning project. 
    This includes handling missing values, encoding categorical variables, feature scaling, and splitting data into training and test sets.
    
    Model evaluation metrics help us understand how well our models perform. 
    Common metrics include accuracy, precision, recall, F1 score, and area under the ROC curve.
    
    In conclusion, machine learning is a powerful technology that continues to evolve rapidly. 
    Understanding these fundamentals will help you build better models and solve real-world problems effectively.
    """.strip()
    
    config = ChunkingConfig(max_chars=300, max_sentences=3)
    chunker = TextChunker(config)
    
    print(f"\nNarration length: {len(long_narration)} characters")
    print(f"Estimated total duration: {chunker.estimate_speech_duration(long_narration, 'English'):.1f} seconds")
    
    chunks = chunker.chunk_text(long_narration, "English")
    
    print(f"\nSplit into {len(chunks)} chunks:")
    total_estimated = 0
    for i, (chunk, duration) in enumerate(chunks):
        total_estimated += duration
        print(f"\n  Chunk {i+1}: {len(chunk)} chars, ~{duration:.1f}s")
        print(f"    Preview: {chunk[:60]}...")
    
    print(f"\nTotal estimated audio duration: {total_estimated:.1f} seconds")
    print(f"Average chunk size: {len(long_narration) // len(chunks)} characters")


if __name__ == "__main__":
    test_basic_chunking()
    test_multilingual()
    test_duration_estimation()
    test_edge_cases()
    test_very_long_text()
    
    print("\n" + "=" * 60)
    print("All tests completed successfully!")
    print("=" * 60)
