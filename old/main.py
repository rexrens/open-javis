import random
import sys

from src.workflow import workflow

# Create and use workflow
if __name__ == "__main__":

    # Fun example topics to showcase the generator's versatility
    example_topics = [
        "The Rise of Artificial General Intelligence: Latest Breakthroughs",
        "How Quantum Computing is Revolutionizing Cybersecurity",
        "Sustainable Living in 2024: Practical Tips for Reducing Carbon Footprint",
        "The Future of Work: AI and Human Collaboration",
        "Space Tourism: From Science Fiction to Reality",
        "Mindfulness and Mental Health in the Digital Age",
        "The Evolution of Electric Vehicles: Current State and Future Trends",
        "Why Cats Secretly Run the Internet",
        "The Science Behind Why Pizza Tastes Better at 2 AM",
        "How Rubber Ducks Revolutionized Software Development",
        "Hello",
        "Hi",
        "Glad to see you",
        "How are you",
        "Who are you?",
        "Nice to meet you.",
        "You look great."
    ]

    topic = ""

    # Check if user provided a topic as a command-line argument
    if len(sys.argv) > 1:
        topic = " ".join(sys.argv[1:])
    else:
        # If no topic is provided, suggest a random one
        random_topic = random.choice(example_topics)
        print(f"No topic provided. Would you like to use this random topic? [Y/n]")
        print(f'Topic: "{random_topic}"')

        try:
            user_choice = input("> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            sys.exit(0)

        if user_choice in ('n', 'no'):
            print("Please enter your own topic:")
            try:
                topic = input("> ").strip()
                if not topic:
                    print("No topic entered. Exiting.")
                    sys.exit(0)
            except (EOFError, KeyboardInterrupt):
                print("\nExiting.")
                sys.exit(0)
        else:
            # Default to using the random topic for 'y', 'yes', or just pressing Enter
            topic = random_topic

    print("-" * 80)
    print(f'Starting workflow for topic: "{topic}"')
    print("-" * 80)

    workflow.print_response(
        input=topic,
        stream=True,
        stream_intermediate_steps=True,
        show_step_details=True
    )