import os
import sqlite3
import bcrypt
import openai
import base64
import uuid
import requests
from langchain.chat_models import ChatOpenAI
from langchain.chains import LLMChain
from langchain.prompts import PromptTemplate
from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS
import people_also_ask as paa
import spacy
from collections import Counter
from datetime import datetime
from perplexity import call_perplexity
import requests
from bs4 import BeautifulSoup
import html2text
import json

#Add your os.apiky here

# Initialize Flask app
app = Flask(__name__)
CORS(app)  # Enable CORS

# Configure image storage
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'images')
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# Load spaCy's English model
nlp = spacy.load("en_core_web_sm")

def init_db():
    conn = sqlite3.connect("users.db")
    c = conn.cursor()

    # Create users table
    c.execute('''CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password TEXT NOT NULL
                )''')

    # Create articles table with additional image_url and meta_title columns
    c.execute('''CREATE TABLE IF NOT EXISTS articles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    article TEXT NOT NULL,
                    title TEXT NOT NULL,
                    entities TEXT,
                    image_url TEXT,
                    meta_title TEXT,
                    created_time_ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_time_ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_deleted BOOLEAN DEFAULT FALSE,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )''')
                
    # Create user_settings table
    c.execute('''CREATE TABLE IF NOT EXISTS user_settings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    settings TEXT NOT NULL,
                    created_time_ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_time_ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_deleted BOOLEAN DEFAULT FALSE,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )''')
    conn.commit()
    conn.close()

# Helper function to add a new user
def add_user(username, password):
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    hashed_pw = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
    try:
        c.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, hashed_pw))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

# Helper function to check login credentials
def check_user(username, password):
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    c.execute("SELECT password FROM users WHERE username = ?", (username,))
    result = c.fetchone()
    conn.close()
    if result and bcrypt.checkpw(password.encode("utf-8"), result[0]):
        return True
    return False

def add_article(username, article, title, entities=None, image_url=None, meta_title=None):
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    try:
        # Get the user_id from the username
        c.execute("SELECT id FROM users WHERE username = ?", (username,))
        user = c.fetchone()

        if user is None:
            return False  # User not found

        user_id = user[0]

        # Insert the article with user_id, image_url, and meta_title
        c.execute('''INSERT INTO articles (user_id, article, title, entities, image_url, meta_title, created_time_ts, updated_time_ts, is_deleted)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                  (user_id, article, title, ", ".join(entities), image_url, meta_title, datetime.now(), datetime.now(), False))
        conn.commit()
        return True
    except Exception as e:
        print(f"Error adding article: {e}")
        return False
    finally:
        conn.close()

def update_article(user_id, article_id, new_article, new_title=None, new_entities=None):
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    print(user_id, article_id, new_article, new_title, new_entities)
    try:
        # Update only if user_id matches to ensure the user is authorized
        c.execute('''UPDATE articles
                     SET article = ?, title = ?, entities = ?, updated_time_ts = ?
                     WHERE id = ? AND user_id = ?''',
                  (new_article, new_title, new_entities, datetime.now(), article_id, user_id))
        conn.commit()
        return True
    except Exception as e:
        print(f"Error updating article: {e}")
        return False
    finally:
        conn.close()

def delete_article(user_id, article_id):
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    try:
        # Set is_deleted to True only if the user_id matches
        c.execute('''UPDATE articles
                     SET is_deleted = TRUE, updated_time_ts = ?
                     WHERE id = ? AND user_id = ?''',
                  (datetime.now(), article_id, user_id))
        conn.commit()
        return True
    except Exception as e:
        print(f"Error deleting article: {e}")
        return False
    finally:
        conn.close()

def get_user_active_articles(user_id):
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    try:
        # Select all articles for the given user that are not marked as deleted, ordered by creation time in descending order
        c.execute('''SELECT id, title, entities, created_time_ts, updated_time_ts, image_url
                     FROM articles
                     WHERE user_id = ? AND is_deleted = FALSE
                     ORDER BY created_time_ts DESC''', (user_id,))
        articles = c.fetchall()
        return articles
    except Exception as e:
        print(f"Error retrieving articles: {e}")
        return []
    finally:
        conn.close()

# Entity extraction function
def extract_top_entities(article_content):
    doc = nlp(article_content)
    entities = [ent.text for ent in doc.ents]
    top_entities = [item[0] for item in Counter(entities).most_common(10) if (len(item[0]) > 3 and (not item[0].isnumeric()))]
    return top_entities

# Function to generate a meta title for the image
def generate_meta_title(article_content):
    prompt = f"Create a concise and descriptive meta title for an image that illustrates the main theme of the following article:\n\n{article_content[:500]}...\n\nMeta title:"

    response = openai.ChatCompletion.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": prompt}
        ],
        max_tokens=10,
        temperature=0.5
    )
    return response.choices[0].message['content'].strip()

# Article generation function
def generate_article(input_data):
    try:
        # Scrape content from the inbound link
        reference_content = ""
        if input_data.get("inbound_link"):
            scraped_content = scrape_url_content(input_data["inbound_link"])
            if scraped_content:
                reference_content = f"\nReference Content: {scraped_content}\n"

        # Create the prompt template with all the settings
        prompt_template = """Write an article about {main_keyword}. 
        Title: {title}
        Keywords to include: {key_words}""" + reference_content + """
        Language: {language}
        Article Size: {article_size}
        Tone of Voice: {tone_of_voice}
        Point of View: {point_of_view}
        Target Country: {target_country}
        Target State: {target_state}
        Target City/ZIP: {target_city_zip}
        Content Guidelines:
        - Write in a {tone_of_voice} tone from a {point_of_view} perspective.
        """

        if input_data.get("tone_of_voice") == "Custom" and input_data.get("custom_tone_data"):
            prompt_template += f"""
            Use the following text as a reference for the tone and style:
            ---
            {input_data['custom_tone_data']}
            ---
            Analyze the tone, style, and writing patterns in the above text and write the article maintaining a similar tone and style.
            """
        # Add prompt filtering guidelines
        prompt_template += "\nContent Filtering Requirements:\n"
        if input_data.get("no_harmful_content", True):
            prompt_template += "- Ensure the content is safe and non-harmful, avoiding any content that could cause harm or distress.\n"
        if input_data.get("no_competitor_content", False):
            prompt_template += "- Avoid mentioning or promoting competitor products, services, or brands.\n"
        if input_data.get("family_friendly", True):
            prompt_template += "- Keep the content family-friendly and appropriate for all audiences.\n"
        if input_data.get("factual_accuracy", True):
            prompt_template += "- Ensure all information is factually accurate and well-researched. Include credible sources where applicable.\n"
        if input_data.get("avoid_bias", True):
            prompt_template += "- Present information objectively, avoiding any cultural, gender, or other biases.\n"

        # Location targeting
        if input_data.get("target_country") or input_data.get("target_state") or input_data.get("target_city_zip"):
            prompt_template += "\nLocation Targeting:\n"
            prompt_template += "- Tailor the content for the audience in {target_country}"
            if input_data.get("target_state"):
                prompt_template += ", {target_state}"
            if input_data.get("target_city_zip"):
                prompt_template += ", {target_city_zip}"
            prompt_template += ".\n"

        # Article structure options
        options = []
        if input_data.get("toc", False):
            options.append("a table of contents")
        if input_data.get("h3", False):
            options.append("H3 headings")
        if input_data.get("quotes", False):
            options.append("relevant quotes")
        if input_data.get("key_takeaways", False):
            options.append("key takeaways")
        if input_data.get("conclusion", False):
            options.append("a summary")

        if options:
            prompt_template += "\nStructural Requirements:\n- Include " + ", ".join(options) + ".\n"

        # Final formatting instructions
        prompt_template += """
            Output Format Requirements:
            - Provide the content in plain text format, not markdown
            - Include relevant outbound reference links where appropriate
            - Ensure proper citation of sources and facts
            """

        # Add citation requirement for GPT model
        if input_data.get("model") == "gpt":
            prompt_template += "\nAlso add references that you have considered while writing the article with 'Article generated with the following citations: ' as a prefix at the end."

        article_prompt = PromptTemplate(
            input_variables=[
                "main_keyword", "title", "key_words", "inbound_link", "language", "article_size", "tone_of_voice", "point_of_view", "target_country", 
                "target_state", "target_city_zip", "toc", "h3", "quotes", "key_takeaways", "conclusion"
            ],
            template=prompt_template,
        )

        if input_data.get("model") == "gpt":
            llm = ChatOpenAI(temperature=0.7, model="gpt-4o")
            article_chain = LLMChain(llm=llm, prompt=article_prompt)
            article_text = article_chain.run(input_data)
            return article_text
        else:  # llama model
            response = call_perplexity(article_prompt.format(**input_data))
            outbound_links = "\nArticle generated with the following citations: \n" + "\n".join(response['citations'])
            article_text = response['choices'][0]['message']['content']
            return article_text + outbound_links

    except Exception as e:
        print(f"Error generating article: {e}")
        return "Failed to generate article"

# Article generation function using Perplexity
def generate_article_perplexity(input_data):
    prompt_template = """
    Write a comprehensive article with the following specifications:
    - Language: {language}
    - Article Size: {article_size}
    - Title: {title}
    - Keywords to include: {key_words}
    - Inbound Link to reference: {inbound_link}
    - Tone of Voice: {tone_of_voice}
    - Point of View: {point_of_view}
    """

    if input_data["tone_of_voice"] == "Custom" and input_data.get("custom_tone_data"):
        prompt_template += "\nUse this tone and style: {custom_tone_data}"

    # Add optional elements
    if input_data["toc"] == "true":
        prompt_template += "\n- Include a table of contents"
    if input_data["h3"] == "true":
        prompt_template += "\n- Use H3 headings for sections"
    if input_data["quotes"] == "true":
        prompt_template += "\n- Include relevant quotes"
    if input_data["key_takeaways"] == "true":
        prompt_template += "\n- Add key takeaways"
    if input_data["conclusion"] == "true":
        prompt_template += "\n- End with a conclusion"

    # Format the prompt with actual values
    prompt = prompt_template.format(
        language=input_data["language"],
        article_size=input_data["article_size"],
        title=input_data["title"],
        key_words=input_data["key_words"],
        inbound_link=input_data["inbound_link"],
        tone_of_voice=input_data["tone_of_voice"],
        point_of_view=input_data["point_of_view"],
        custom_tone_data=input_data.get("custom_tone_data", "")
    )

    # Call Perplexity API
    response = call_perplexity(prompt)
    if response and 'choices' in response and len(response['choices']) > 0:
        article_text = response['choices'][0]['message']['content']
        if 'citations' in response:
            outbound_links = "\nArticle generated with the following citations: \n" + "\n".join(response['citations'])
            article_text += outbound_links
        return article_text
    return "Failed to generate article with Perplexity"

# Function to save a base64 image
def save_base64_image(base64_string):
    try:
        # Generate a unique filename
        filename = f"{uuid.uuid4()}.png"
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        
        # Remove the data URL prefix if present
        if ',' in base64_string:
            base64_string = base64_string.split(',')[1]
        
        # Decode and save the image
        image_data = base64.b64decode(base64_string)
        with open(filepath, 'wb') as f:
            f.write(image_data)
            
        return filename
    except Exception as e:
        print(f"Error saving image: {e}")
        return None

# Function to generate an image
def generate_image(prompt):
    try:
        response = openai.Image.create(
            prompt=prompt,
            n=1,
            size="512x512"
        )
        
        # Get the base64 image
        image_url = response['data'][0]['url']
        image_response = requests.get(image_url)
        if image_response.status_code == 200:
            # Convert to base64
            base64_image = base64.b64encode(image_response.content).decode('utf-8')
            # Save image and get filename
            filename = save_base64_image(base64_image)
            if filename:
                return filename
        return None
    except Exception as e:
        print(f"Error generating image: {e}")
        return None

# API endpoint to serve images
@app.route('/images/<path:filename>')
def serve_image(filename):
    try:
        return send_from_directory(UPLOAD_FOLDER, filename)
    except Exception as e:
        return jsonify({"error": str(e)}), 404

# API for user login
@app.route("/login", methods=["POST"])
def login():
    data = request.get_json()
    username = data.get("username")
    password = data.get("password")

    if not username or not password:
        return jsonify({"message": "Username and password are required"}), 400

    if check_user(username, password):
        return jsonify({"message": "Login successful!"}), 200
    else:
        return jsonify({"message": "Invalid username or password"}), 401

# API for user registration
@app.route("/register", methods=["POST"])
def register():
    data = request.get_json()
    username = data.get("username")
    password = data.get("password")

    if not username or not password:
        return jsonify({"message": "Username and password are required"}), 400

    if add_user(username, password):
        return jsonify({"message": "Registration successful!"}), 201
    else:
        return jsonify({"message": "Username already exists"}), 409

# API for article and image generation
@app.route("/generate-article", methods=["POST"])
def generate_prompt():
    try:
        data = request.json
        username = data.get("username")
        rows = data.get("rows", [])
        settings = data.get("settings", {})
        results = []

        if not username or not rows:
            return jsonify({"error": "Missing required data"}), 400

        for row_data in rows:
            input_data = {
                "main_keyword": row_data.get("mainKeyword", ""),
                "title": row_data.get("title", ""),
                "key_words": row_data.get("keywords", ""),
                "inbound_link": row_data.get("inboundLink", ""),
                "language": settings.get("language", "english"),
                "article_size": settings.get("articleSize", "short"),
                "tone_of_voice": settings.get("toneOfVoice", "Natural"),
                "custom_tone_data": settings.get("custom_tone_data"),
                "point_of_view": settings.get("pointOfView", "first person"),
                "target_country": settings.get("targetCountry", ""),
                "target_state": settings.get("targetState", ""),
                "target_city_zip": settings.get("targetCityZip", ""),
                "model": settings.get("model", "gpt"),
                "toc": settings.get("toc", False),
                "h3": settings.get("h3", False),
                "quotes": settings.get("quotes", False),
                "key_takeaways": settings.get("keyTakeaways", False),
                "conclusion": settings.get("conclusion", False),
                "generate_image": settings.get("generateImage", False),
                "no_harmful_content": settings.get("noHarmfulContent", True),
                "no_competitor_content": settings.get("noCompetitorContent", True),
                "family_friendly": settings.get("familyFriendly", True),
                "factual_accuracy": settings.get("factualAccuracy", True),
                "avoid_bias": settings.get("avoidBias", True)
            }

            # Choose the model based on the model parameter
            if data.get("model") == "perplexity":
                article = generate_article_perplexity(input_data)
            else:  # default to GPT
                article = generate_article(input_data)

            # Extract entities from the generated article
            entities = extract_top_entities(article)

            # Generate image if requested
            image_filename = None
            meta_title = None
            print(input_data["generate_image"])
            if input_data["generate_image"]:
                print("Generating image...")
                image_prompt = f"Create an image related to the topic: {input_data['title']}"
                try:
                    meta_title = generate_meta_title(article)
                    image_filename = generate_image(image_prompt)
                except Exception as e:
                    print(f"Error generating image: {e}")

            # Store the article in the database
            add_article(username, article, row_data["title"], entities, image_filename, meta_title)

            results.append({
                "title": row_data["title"],
                "article": article,
                "entities": entities,
                "image_url": f"/images/{image_filename}" if image_filename else None
            })

        return jsonify({
            "message": "Articles generated successfully!",
            "data": results
        }), 200

    except Exception as e:
        print(f"Error generating articles: {e}")
        return jsonify({"message": f"Error generating articles: {str(e)}"}), 500

@app.route("/update-article", methods=["POST"])
def update_article_api():
    article_id = request.args.get("id", type=int)
    input_data = request.get_json()
    username = input_data["username"]
    new_article = input_data.get("new_article")
    new_title = input_data.get("new_title")
    new_entities = input_data.get("new_entities")

    # Get user_id from username
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    c.execute("SELECT id FROM users WHERE username = ?", (username,))
    user = c.fetchone()
    conn.close()

    if user is None:
        return jsonify({"message": "User not found"}), 404

    user_id = user[0]

    if update_article(user_id, article_id, new_article, new_title, new_entities):
        return jsonify({"message": "Article updated successfully!"})
    else:
        return jsonify({"message": "Error updating article"}), 400

@app.route("/delete-article", methods=["DELETE"])
def delete_article_api():
    try:
        # Get parameters from query string or request body
        id = request.args.get("id")
        username = request.args.get("username")

        # Convert id to integer
        try:
            id = int(id) if id else None
        except ValueError:
            return jsonify({"message": "Invalid article ID format"}), 400

        # Validate inputs
        if id is None or username is None:
            return jsonify({"message": "Missing required parameters. Both 'id' and 'username' are required."}), 400

        # Get user_id from username
        conn = sqlite3.connect("users.db")
        c = conn.cursor()
        c.execute("SELECT id FROM users WHERE username = ?", (username,))
        user = c.fetchone()
        conn.close()

        if user is None:
            return jsonify({"message": f"User '{username}' not found"}), 404

        user_id = user[0]

        # Verify article exists and belongs to user
        conn = sqlite3.connect("users.db")
        c = conn.cursor()
        c.execute("SELECT id FROM articles WHERE id = ? AND user_id = ? AND is_deleted = FALSE", (id, user_id))
        article = c.fetchone()
        conn.close()

        if article is None:
            return jsonify({"message": f"Article with id {id} not found or already deleted"}), 404

        if delete_article(user_id, id):
            return jsonify({"message": "Article deleted successfully", "article_id": id})
        else:
            return jsonify({"message": "Failed to delete article. Please try again."}), 500

    except Exception as e:
        print(f"Error in delete_article_api: {e}")
        return jsonify({"message": f"Server error: {str(e)}"}), 500

@app.route("/download-article", methods=["GET"])
def download_article():
    article_id = request.args.get("id", type=int)

    # Validate input
    if article_id is None:
        return jsonify({"message": "Missing 'id'"}), 400

    # Fetch article content from database
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    c.execute("SELECT article FROM articles WHERE id = ?", (article_id,))
    article = c.fetchone()
    conn.close()

    if article is None:
        return jsonify({"message": "Article not found"}), 404

    article_content = article[0]

    # Create a temporary text file with the article content
    filename = f"article_{article_id}.txt"
    filepath = os.path.join("/tmp", filename)
    with open(filepath, "w") as f:
        f.write(article_content)

    # Send the file as an attachment
    return send_file(filepath, as_attachment=True, download_name=filename)

@app.route("/fetch-single-article", methods=["GET"])
def fetch_single_article():
    article_id = request.args.get("id", type=int)

    # Validate input
    if article_id is None:
        return jsonify({"message": "Missing 'id' parameter"}), 400

    # Connect to the database
    conn = sqlite3.connect("users.db")
    conn.row_factory = sqlite3.Row  # This allows accessing columns by name
    c = conn.cursor()

    # Fetch the article
    c.execute("""
        SELECT id, user_id, article, title, entities, image_url, meta_title,
               created_time_ts, updated_time_ts, is_deleted
        FROM articles
        WHERE id = ? AND is_deleted = 0
    """, (article_id,))
    article = c.fetchone()

    # Close the database connection
    conn.close()

    # if article is None:
    #     return jsonify({"message": "Article not found"}), 404

    # Convert the SQLite Row object to a dictionary
    article_data = dict(article)
    # print(article_data, "\n---------------------------------------------------------------")

    # Parse the entities JSON string to a Python object
    # if article_data['entities']:
    #     article_data['entities'] = json.loads(article_data['entities'])

    # # Convert is_deleted to boolean
    # article_data['is_deleted'] = bool(article_data['is_deleted'])

    return article_data

@app.route("/fetch-generated-history", methods=["GET"])
def fetch_generated_history():
    username = request.args.get("username")
    if not username:
        return jsonify({"message": "Username is required"}), 400

    # Get user_id from username
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    c.execute("SELECT id FROM users WHERE username = ?", (username,))
    user = c.fetchone()
    conn.close()

    if user is None:
        return jsonify({"message": "User not found"}), 404

    user_id = user[0]
    articles = get_user_active_articles(user_id)
    return jsonify({
        "message": "Fetched active articles successfully",
        "data": articles
    })

def get_entities_template(data):
    template = (
        "Can you extract the important key words from the given input text {text} or for the given title {title} of the article? "
        "Limit yourself for maximum 5 key words. "
        "Your output should be only a list of key words, don't include any other text."
    )
    return template.format(**data)

# Generate the article based on the input
def generate_entities(answer):
    data = {"title": answer["question"]}
    data["text"] = ""
    if answer["has_answer"]:
        data["text"] = answer["raw_text"]
    template = get_entities_template(data)

    response = openai.ChatCompletion.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": template}
        ],
        )
    # Extract the generated article text
    entities = response.choices[0].message['content'].strip()
    return entities

# API to get related questions
@app.route("/get-related-questions", methods=["POST"])
def related_questions():
    input_data = request.get_json()
    main_keyword = input_data.get("main_keyword")
    number_of_questions = input_data.get("number_of_questions")
    print(input_data)
    print(main_keyword)
    print(number_of_questions)
    related_questions = paa.get_related_questions(main_keyword, number_of_questions-1)
    print(related_questions)
    data = [{"question": q, "key_words": generate_entities(paa.get_answer(q)), "answer": paa.get_answer(q)} for q in related_questions]
    return jsonify({"message": "Successful!", "data": data})

def scrape_url_content(url):
    try:
        # Send a GET request to the URL
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        # Parse the HTML content
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Remove script and style elements
        for script in soup(["script", "style"]):
            script.decompose()
            
        # Convert HTML to plain text
        h = html2text.HTML2Text()
        h.ignore_links = True
        h.ignore_images = True
        text = h.handle(str(soup))
        
        # Clean up the text
        text = ' '.join(text.split())
        
        # Limit the text length to avoid token limits
        max_chars = 2000
        if len(text) > max_chars:
            text = text[:max_chars] + "..."
            
        return text
    except Exception as e:
        print(f"Error scraping URL: {e}")
        return None

# Helper function to get user id from username
def get_user_id(username):
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    c.execute("SELECT id FROM users WHERE username = ?", (username,))
    result = c.fetchone()
    conn.close()
    return result[0] if result else None

# Save user settings preset
@app.route("/save-settings", methods=["POST"])
def save_settings():
    try:
        data = request.json
        username = data.get("username")
        name = data.get("name")
        settings = data.get("settings")

        if not all([username, name, settings]):
            return jsonify({"error": "Missing required data"}), 400

        user_id = get_user_id(username)
        if not user_id:
            return jsonify({"error": "User not found"}), 404

        conn = sqlite3.connect("users.db")
        c = conn.cursor()

        # Check if preset name already exists for this user
        c.execute("SELECT id FROM user_settings WHERE user_id = ? AND name = ? AND is_deleted = FALSE", (user_id, name))
        existing_preset = c.fetchone()

        if existing_preset:
            # Update existing preset
            c.execute("""
                UPDATE user_settings 
                SET settings = ?, updated_time_ts = CURRENT_TIMESTAMP 
                WHERE id = ?
            """, (json.dumps(settings), existing_preset[0]))
        else:
            # Create new preset
            c.execute("""
                INSERT INTO user_settings (user_id, name, settings) 
                VALUES (?, ?, ?)
            """, (user_id, name, json.dumps(settings)))

        conn.commit()
        conn.close()

        return jsonify({"message": "Settings saved successfully"}), 200

    except Exception as e:
        print(f"Error saving settings: {e}")
        return jsonify({"error": str(e)}), 500

# Get user settings presets
@app.route("/get-settings", methods=["GET"])
def get_settings():
    try:
        username = request.args.get("username")
        if not username:
            return jsonify({"error": "Username is required"}), 400

        user_id = get_user_id(username)
        if not user_id:
            return jsonify({"error": "User not found"}), 404

        conn = sqlite3.connect("users.db")
        c = conn.cursor()
        c.execute("""
            SELECT id, name, settings, created_time_ts, updated_time_ts 
            FROM user_settings 
            WHERE user_id = ? AND is_deleted = FALSE
            ORDER BY updated_time_ts DESC
        """, (user_id,))
        
        presets = []
        for row in c.fetchall():
            presets.append({
                "id": row[0],
                "name": row[1],
                "settings": json.loads(row[2]),
                "created_time_ts": row[3],
                "updated_time_ts": row[4]
            })

        conn.close()
        return jsonify({"presets": presets}), 200

    except Exception as e:
        print(f"Error getting settings: {e}")
        return jsonify({"error": str(e)}), 500

# Delete user settings preset
@app.route("/delete-settings", methods=["DELETE"])
def delete_settings():
    try:
        data = request.json
        username = data.get("username")
        preset_id = data.get("preset_id")

        if not all([username, preset_id]):
            return jsonify({"error": "Missing required data"}), 400

        user_id = get_user_id(username)
        if not user_id:
            return jsonify({"error": "User not found"}), 404

        conn = sqlite3.connect("users.db")
        c = conn.cursor()

        # Soft delete the preset
        c.execute("""
            UPDATE user_settings 
            SET is_deleted = TRUE, updated_time_ts = CURRENT_TIMESTAMP 
            WHERE id = ? AND user_id = ?
        """, (preset_id, user_id))

        if c.rowcount == 0:
            conn.close()
            return jsonify({"error": "Preset not found"}), 404

        conn.commit()
        conn.close()

        return jsonify({"message": "Settings deleted successfully"}), 200

    except Exception as e:
        print(f"Error deleting settings: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    init_db()  # Initialize the database before starting the app
    app.run(debug=True)
