# =========================================================================================
# A.3.T.H.E.R ENGINE — BACKEND
# PHASE 2.4 — MEMORY CORE ENGINE
# SQLITE DATABASE / LONG TERM MEMORY / CONTEXT SYSTEM
# Adaptive 3rd-generation Technology for Heuristic Execution & Research
# AL13N INDUSTRIES
# =========================================================================================


import sqlite3
import os
from datetime import datetime




# ===============================
# MEMORY CONFIGURATION
# ===============================


MEMORY_SYSTEM = {


    "name":
    "AETHER Memory Vault",


    "status":
    "OFFLINE",


    "stored":
    0


}




# ===============================
# DATABASE PATH
# ===============================


try:
    from config.paths import data_path as _data_path

    DATABASE_PATH = str(_data_path("backend/database/memory.db"))
except Exception:  # noqa: BLE001
    DATABASE_PATH = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "database",
        "memory.db",
    )






# ===============================
# DATABASE CONNECTION
# ===============================


def ConnectDatabase():


    folder = os.path.dirname(
        DATABASE_PATH
    )


    os.makedirs(
        folder,
        exist_ok=True
    )


    return sqlite3.connect(
        DATABASE_PATH
    )








# ===============================
# INITIALIZE MEMORY
# ===============================


def InitializeMemory():


    db = ConnectDatabase()

    cursor = db.cursor()



    cursor.execute(
    """

    CREATE TABLE IF NOT EXISTS memories

    (

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        user TEXT,

        assistant TEXT,

        category TEXT,

        timestamp TEXT

    )

    """
    )



    db.commit()

    db.close()



    MEMORY_SYSTEM["status"] = "ONLINE"



    print(
        "[MEMORY] Memory Vault ONLINE"
    )








# ===============================
# SAVE MEMORY
# ===============================


def SaveMemory(data):


    db = ConnectDatabase()

    cursor = db.cursor()



    cursor.execute(

    """

    INSERT INTO memories

    (

    user,

    assistant,

    category,

    timestamp

    )

    VALUES

    (?, ?, ?, ?)

    """,

    (

        data.get(
            "user",
            ""
        ),


        data.get(
            "assistant",
            ""
        ),


        data.get(
            "category",
            "conversation"
        ),


        datetime.now()
        .isoformat()

    )

    )



    db.commit()

    db.close()



    MEMORY_SYSTEM["stored"] += 1






# ===============================
# GET RECENT MEMORY
# ===============================


def GetMemories(limit=10):


    db = ConnectDatabase()

    cursor = db.cursor()



    cursor.execute(

    """

    SELECT *

    FROM memories

    ORDER BY id DESC

    LIMIT ?

    """,

    (limit,)

    )



    result = cursor.fetchall()



    db.close()



    return result








# ===============================
# SEARCH MEMORY
# ===============================


def SearchMemory(query):


    db = ConnectDatabase()

    cursor = db.cursor()



    cursor.execute(

    """

    SELECT *

    FROM memories

    WHERE user LIKE ?

    OR assistant LIKE ?

    """,

    (

        "%" + query + "%",

        "%" + query + "%"

    )

    )



    result = cursor.fetchall()



    db.close()



    return result







# ===============================
# LOAD AI CONTEXT
# ===============================


def LoadContext():


    memories = GetMemories(
        10
    )


    context=[]



    for item in memories:


        context.append({

            "user":
            item[1],


            "assistant":
            item[2]

        })



    return context








# ===============================
# MEMORY STATUS
# ===============================


def GetMemoryStatus():


    return {


        "system":
        MEMORY_SYSTEM["name"],


        "status":
        MEMORY_SYSTEM["status"],


        "stored":
        MEMORY_SYSTEM["stored"],


        "database":
        DATABASE_PATH


    }








# ===============================
# TEST
# ===============================


if __name__ == "__main__":


    InitializeMemory()



    SaveMemory({

        "user":
        "System test",


        "assistant":
        "Memory working"

    })



    print(
        GetMemories()
    )


    print(
        GetMemoryStatus()
    )