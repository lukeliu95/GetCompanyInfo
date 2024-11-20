from flask import Flask, render_template, request, jsonify
import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import datetime
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
import time

app = Flask(__name__)

# 添加模板全局函数
app.jinja_env.globals.update(min=min, max=max)

# 设置日志记录
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# 创建一个文件处理器
file_handler = RotatingFileHandler('search.log', maxBytes=10240, backupCount=5)
file_handler.setLevel(logging.INFO)

# 创建一个控制台处理器
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)

# 创建一个格式器
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)
console_handler.setFormatter(formatter)

# 添加处理器到日志记录器
logger.addHandler(file_handler)
logger.addHandler(console_handler)

# 数据库配置
Base = declarative_base()
engine = create_engine('sqlite:///search_results.db')
Session = sessionmaker(bind=engine)

class SearchResult(Base):
    __tablename__ = 'search_results'
    
    id = Column(Integer, primary_key=True)
    keyword = Column(String)
    url = Column(String)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

# 创建数据库表
Base.metadata.create_all(engine)

def get_keywords_from_csv(start_index=0, end_index=None):
    try:
        csv_path = os.path.join('static', '02_aomori_all_20241031.csv')
        logger.info(f"Reading CSV file from {csv_path}")
        
        if not os.path.exists(csv_path):
            logger.error(f"CSV file not found at {csv_path}")
            return []
        
        # Try different encodings
        encodings = ['utf-8', 'shift-jis', 'cp932']
        df = None
        
        for encoding in encodings:
            try:
                df = pd.read_csv(csv_path, encoding=encoding)
                logger.info(f"Successfully read CSV with encoding: {encoding}")
                break
            except UnicodeDecodeError:
                logger.warning(f"Failed to read CSV with encoding: {encoding}")
                continue
        
        if df is None:
            logger.error("Failed to read CSV with any encoding")
            return []
        
        # 获取唯一的关键词
        keywords = df['弘前検察審査会'].unique().tolist()
        logger.info(f"Found {len(keywords)} unique keywords")
        
        # 应用起止索引
        if end_index is None:
            end_index = len(keywords)
        
        # 确保索引在有效范围内
        start_index = max(0, min(start_index, len(keywords)))
        end_index = max(0, min(end_index, len(keywords)))
        
        # 确保起始索引小于结束索引
        if start_index >= end_index:
            logger.error(f"Invalid index range: start={start_index}, end={end_index}")
            return []
        
        selected_keywords = keywords[start_index:end_index]
        logger.info(f"Selected {len(selected_keywords)} keywords from index {start_index} to {end_index}")
        
        return selected_keywords
        
    except Exception as e:
        logger.error(f"Error reading CSV file: {str(e)}")
        return []

def search_google(keyword, driver):
    try:
        search_url = f'https://www.google.com/search?q={keyword}+公式サイト'
        logger.info(f"Searching for: {keyword}")
        driver.get(search_url)
        
        wait = WebDriverWait(driver, 10)
        results = wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, 'div.g')))
        
        if not results:
            logger.warning(f"No results found for: {keyword}")
            return None
        
        for result in results[:1]:
            try:
                link = result.find_element(By.CSS_SELECTOR, 'a').get_attribute('href')
                logger.info(f"Found URL for {keyword}: {link}")
                return link
            except Exception as e:
                logger.error(f"Error extracting link for {keyword}: {str(e)}")
                return None
                
    except Exception as e:
        logger.error(f"Error searching for {keyword}: {str(e)}")
        return None

@app.route('/')
def index():
    try:
        return render_template('index.html')
    except Exception as e:
        logger.error(f"Error rendering template: {str(e)}")
        return jsonify({"error": "Internal server error"}), 500

@app.route('/start_search', methods=['POST'])
def start_search():
    try:
        start_index = int(request.form.get('start_index', 0))
        end_index = request.form.get('end_index', '')
        end_index = int(end_index) if end_index.strip() else None
        
        logger.info(f"Starting search with range: {start_index} to {end_index}")
        
        keywords = get_keywords_from_csv(start_index, end_index)
        if not keywords:
            return jsonify({"error": "No keywords found in specified range"}), 400
        
        # Setup Chrome options
        chrome_options = Options()
        chrome_options.add_argument('--headless=new')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--lang=ja_JP')
        
        # 修改 Chrome WebDriver 的初始化方式
        driver = webdriver.Chrome(options=chrome_options)
        
        session = Session()
        
        try:
            total_keywords = len(keywords)
            for i, keyword in enumerate(keywords, 1):
                try:
                    # 每次搜索前等待10秒
                    if i > 1:  # 第一次搜索不需要等待
                        logger.info(f"Waiting 5 seconds before next search...")
                        time.sleep(5)
                    
                    url = search_google(keyword, driver)
                    if url:
                        result = SearchResult(keyword=keyword, url=url)
                        session.add(result)
                        if i % 5 == 0:  # 每5条记录提交一次
                            session.commit()
                    
                    logger.info(f"Processed {i}/{total_keywords}: {keyword}")
                    
                except Exception as e:
                    logger.error(f"Error processing keyword {keyword}: {str(e)}")
                    continue
            
            session.commit()
            logger.info("Search completed successfully")
            return jsonify({"success": True, "redirect": "/results"})
            
        except Exception as e:
            logger.error(f"Error during search process: {str(e)}")
            session.rollback()
            return jsonify({"error": str(e)}), 500
        finally:
            session.close()
            driver.quit()
            
    except Exception as e:
        logger.error(f"Error in start_search: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/results')
def view_results():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    sort_by = request.args.get('sort_by', 'created_at')
    order = request.args.get('order', 'desc')
    
    session = Session()
    try:
        # 构建查询
        query = session.query(SearchResult)
        
        # 应用排序
        if order == 'desc':
            query = query.order_by(getattr(SearchResult, sort_by).desc())
        else:
            query = query.order_by(getattr(SearchResult, sort_by).asc())
        
        # 计算总记录数和总页数
        total_records = query.count()
        total_pages = (total_records + per_page - 1) // per_page
        
        # 获取当前页的数据
        results = query.offset((page - 1) * per_page).limit(per_page).all()
        
        return render_template(
            'results.html',
            results=results,
            page=page,
            per_page=per_page,
            total_pages=total_pages,
            total_records=total_records,
            sort_by=sort_by,
            order=order
        )
    except Exception as e:
        logger.error(f"Error in view_results: {str(e)}")
        return jsonify({"error": str(e)}), 500
    finally:
        session.close()

if __name__ == '__main__':
    try:
        logger.info("Starting Flask application")
        app.run(host='0.0.0.0', port=5001)
    except Exception as e:
        logger.error(f"Error starting application: {str(e)}")
