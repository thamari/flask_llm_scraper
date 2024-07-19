import time
from flask import Flask, request, jsonify
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
import re
from dotenv import load_dotenv
from typing import Optional, List
from langchain_core.pydantic_v1 import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate
from langchain_mistralai import ChatMistralAI
import mysql.connector
import os

# Load env variables
load_dotenv()

app = Flask(__name__)
# Configure MySQL connection
db_config = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME')
}
# Selenium setup
chrome_driver_path = os.getenv('CHROME_DRIVER_PATH')
service = Service(chrome_driver_path)

options = webdriver.ChromeOptions()
options.add_argument('--headless')
options.add_argument('--no-sandbox')
options.add_argument('--disable-dev-shm-usage')
options.add_argument('--disable-extensions')
options.add_argument('--disable-infobars')
options.add_argument('--disable-notifications')
options.add_argument('--incognito')
options.add_argument('start-maximized')
options.add_argument('disable-blink-features=AutomationControlled')

class Artist(BaseModel):
    """Information about an artist."""
    name: str = Field(description="The name of the artist")
    instrument: Optional[str] = Field(default=None, description="The instrument played by the artist (if known)")

class Program(BaseModel):
    """Information about a program piece."""
    title: str = Field(description="The title of the program piece")
    composer: str = Field(description="The composer of the program piece")

class Event(BaseModel):
    """Information about the event."""
    name: str = Field(description="The name of the event")
    date: str = Field(description="The date of the event")
    time: str = Field(description="The time of the event")
    venue_name: str = Field(description="The name of the venue")
    venue_address: str = Field(description="The address of the venue")
    patron_services_phone: str = Field(description="The phone number for patron services")
    patron_services_email: str = Field(description="The email address for patron services")
    artists: List[Artist] = Field(default_factory=list, description="List of artists performing")
    programs: List[Program] = Field(default_factory=list, description="List of program pieces")
    music_director: str = Field(description="The music director for the event")
    event_description: str = Field(description="A description of the event")
    ticket_status: str = Field(description="The current ticket status for the event")

def create_extraction_chain():
    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are an information extraction assistant. Please extract the following information from the text: \n"
                   "1. Event name\n2. Date\n3. Time\n4. Venue name\n5. Venue address\n"
                   "6. Patron services contact phone\n7. Patron services contact email\n"
                   "8. Artists (name and instrument)\n9. Program (title and composer)\n"
                   "10. Music director\n11. Event description\n12. Ticket status"),
        ("human", "{text}"),
    ])
    llm = ChatMistralAI(model="mistral-large-latest", temperature=0)
    return prompt | llm.with_structured_output(schema=Event)

def extract_info(text: str, chain=None):
    if not chain:
        chain = create_extraction_chain()
    output = chain.invoke({"text": text})
    extracted_info = output.dict()
    return extracted_info

@app.route('/api/save-entity')
def save_entity():
    url = request.args.get('url')
    if not url:
        return jsonify({"error": "URL parameter is required"}), 400

    driver = webdriver.Chrome(service=service, options=options)
    try:
        driver.get(url)
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, 'body')))
        content = driver.page_source
    finally:
        driver.quit()

    soup = BeautifulSoup(content, 'html.parser')
    text_content = soup.get_text(separator=' ')
    text_content = text_content.replace('\n', '')
    text_content = re.sub(r"\s+", " ", text_content)

    extracted_info = extract_info(text_content)

    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS EntitiesMaster (
            id INT AUTO_INCREMENT PRIMARY KEY,
            url VARCHAR(255),
            event_name VARCHAR(255),
            event_date VARCHAR(50),
            event_time VARCHAR(50),
            venue_name VARCHAR(255),
            venue_address TEXT,
            patron_services_phone VARCHAR(20),
            patron_services_email VARCHAR(100),
            music_director VARCHAR(100),
            event_description TEXT,
            ticket_status VARCHAR(50)
        )
        """)

        insert_query = """
        INSERT INTO EntitiesMaster (url, event_name, event_date, event_time, venue_name, 
        venue_address, patron_services_phone, patron_services_email, music_director, 
        event_description, ticket_status) 
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        cursor.execute(insert_query, (url, extracted_info['name'], extracted_info['date'], 
                                      extracted_info['time'], extracted_info['venue_name'], 
                                      extracted_info['venue_address'], 
                                      extracted_info['patron_services_phone'], 
                                      extracted_info['patron_services_email'], 
                                      extracted_info['music_director'], 
                                      extracted_info['event_description'], 
                                      extracted_info['ticket_status']))
        
        conn.commit()
    except mysql.connector.Error as err:
        return jsonify({"error": f"Database error: {err}"}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()

    return jsonify({"message": "Entities saved successfully", "data": extracted_info})

@app.route('/api/get-entity')
def get_entity():
    url = request.args.get('url')
    if not url:
        return jsonify({"error": "URL parameter is required"}), 400

    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        
        query = "SELECT * FROM EntitiesMaster WHERE url = %s"
        cursor.execute(query, (url,))
        
        result = cursor.fetchone()
        cursor.nextset()  # This clears any remaining result sets

        if result:
            return jsonify(result)
        else:
            return jsonify({"message": "No data found for the given URL"}), 404
    except mysql.connector.Error as err:
        return jsonify({"error": f"Database error: {err}"}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()



if __name__ == '__main__':
    app.run(debug=True)
