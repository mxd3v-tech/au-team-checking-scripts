#!/usr/bin/env python3
import requests
import time
import os
import json

def get_script_dir():
    return os.path.dirname(os.path.abspath(__file__))

def get_authorized_session():
    """Создает авторизованную сессию с правильными заголовками"""
    session = requests.Session()
    session.headers.update({
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "http://192.168.102.39/",
        "Content-Type": "application/json;charset=UTF-8",
        "Accept": "application/json, text/plain, */*"
    })
    return session

def login():
    """Пытается авторизоваться и сохранить токен"""
    session = get_authorized_session()
    payload = {
        "username": "api",
        "password": "jw0mUEnY3",
        "html": "1"
    }
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = session.post("http://192.168.102.39/store/public/auth/login/login", json=payload)
            
            if response.status_code in [200, 202]:
                try:
                    response_data = response.json()
                    if response_data.get("result") is True and response_data.get("message") == "success":
                        token = session.cookies.get("token")
                        if token:
                            script_dir = get_script_dir()
                            cookies_file = os.path.join(script_dir, "cookies.txt")
                            with open(cookies_file, "w") as f:
                                f.write("# Netscape HTTP Cookie File\n")
                                f.write(f"192.168.102.39\tFALSE\t/\tFALSE\t0\ttoken\t{token}\n")
                            print(f"✅ Авторизация успешна. Токен: {token[:10]}...")
                            return True
                except Exception as e:
                    print(f"❌ Ошибка парсинга ответа: {e}")
            
            print(f"❌ Ошибка авторизации (попытка {attempt+1}/{max_retries})")
            print(f"Статус: {response.status_code}")
            print(f"Тело ответа: {response.text}")
            
            if attempt < max_retries - 1:
                time.sleep(2)  # Пауза между попытками
                
        except Exception as e:
            print(f"❌ Ошибка при авторизации: {e}")
            if attempt < max_retries - 1:
                time.sleep(2)
    
    return False

if __name__ == "__main__":
    if login():
        print("✅ Авторизация завершена")
    else:
        print("❌ Авторизация не удалась")
