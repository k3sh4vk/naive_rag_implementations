# -*- coding: utf-8 -*-
"""003_Agentic_RAG_LLamaIndex

Automatically generated by Colab.

Original file is located at
    https://colab.research.google.com/drive/1rXcUdFtmhgbdhnpQn8MSFRVufrA7yh9u

# We are going to use unstructured and structured data in our data stores to answer our query
# This is possible via an entity called an **agent**.

We are going to get the unstructured data from **wikipedia** and unstructured from the database (which will contain ratings and other quantitative stuff) we will create using a csv.

Install Pre-requisites
"""

!pip install -U -q nest_asyncio openai llama-index llama-index-embeddings-nomic llama-index-readers-wikipedia nltk tiktoken sentence-transformers pydantic wikipedia sqlalchemy pandas python-dotenv

"""Notebook doesn't allow async operations to complete properly. So we will use nest_asyncio for it"""

import nest_asyncio

nest_asyncio.apply()

import logging
import sys

logging.basicConfig(stream=sys.stdout, level=logging.INFO)
logging.getLogger().addHandler(logging.StreamHandler(stream=sys.stdout))

"""INITIATE OPENAI KEYS"""

import os
from pprint import pformat
from dotenv import dotenv_values
env_file_path = "/content/drive/MyDrive/Colab Notebooks/RAG/env.txt"

env_dict = dotenv_values(env_file_path)

"""WE ARE GOING TO USE NOMIC EMBEDDING MODEL FOR THIS TASK FOR WHICH A VERY NICE [CONNECTOR IS PROVIDED BY llama-index team](https://docs.llamaindex.ai/en/latest/examples/embeddings/nomic/)."""

from llama_index.embeddings.nomic import NomicEmbedding


# Nomic has released two models. v1 has fixed dimensionality and v1.5 supports variable dimen
# - nomic-embed-text-v1   | fixed dimensionality
# - nomic-embed-text-v1.5 | variable length dimensionality via matryoshka learning | size range : 64 to 768

embedding_model = NomicEmbedding(
    model_name="nomic-embed-text-v1",
    api_key=env_dict['NOMIC_API_KEY']
)

"""Lets test the embedding model"""

embedding_model.get_text_embedding("Nomic Embedding !")

"""#### Core Settings | Configurations

LlamaIndex has the ability to set `Settings` (Successor of `ServiceContext`). The basic idea here is that we use this to establish some core properties and then can pass it to various services.

While we could set this up as a global, we're going to leave it as `Settings` so we can see where it's applied.

We'll set a few significant contexts:

- `chunk_size` - this is what it says on the tin
- `llm` - this is where we can set what model we wish to use as our primary LLM when we're making `QueryEngine`s and more
- `embed_model` - this will help us keep our embedding model consistent across use cases


We'll also create some resources we're going to keep consistent across all of our indices today.

- `text_splitter` - This is what we'll use to split our text, feel free to experiment here
- `SimpleNodeParser` - This is what will work in tandem with the `text_splitter` to parse our full sized documents into nodes.
"""

from llama_index.llms.openai import OpenAI                  # import Model
from llama_index.core import Settings                       # import Settings
from llama_index.core.node_parser import SentenceSplitter  # import nodeparser variant
from llama_index.core.llms import ChatMessage
from pprint import pformat

# create model instance
# api_version='v1'
# model_version = 'gpt-3.5-unfiltered'
# model_version = 'gpt-3.5-turbo'
model_version = 'pai-001'
language_model = OpenAI(
    api_key=env_dict['OPENAI_API_KEY'],
    api_base=env_dict['OPENAI_BASE_URL'],
    model=model_version
    # api_version=api_version
)

# configure Settings
Settings.llm = language_model
Settings.embed_model = embedding_model
Settings.node_parser = SentenceSplitter(chunk_size=512, chunk_overlap=20)
Settings.context_window = 3900

print(language_model.json())

language_model.chat([ChatMessage(role="user", content="How are you?")])

"""LETS INITIATE A VECTOR STORE FOR OUR DOCS

- We will get the documents from wikipedia, chunk them and store them in
our vector store
"""

from llama_index.core import VectorStoreIndex

index = VectorStoreIndex.from_documents([])

"""READ DATA FROM Wikipedia

Setting `auto_suggest=False` ensures we run into fewer auto-correct based errors.
"""

from llama_index.readers.wikipedia import WikipediaReader

movie_list = ["Barbie (film)", "Oppenheimer (film)"]

wiki_docs = WikipediaReader().load_data(pages=movie_list, auto_suggest=False)

"""Now we will loop through our documents and metadata and construct nodes (associated with particular metadata for easy filtration later)."""

for movie, wiki_doc in zip(movie_list, wiki_docs):
    nodes = Settings.node_parser.get_nodes_from_documents([wiki_doc])

    # add metadata to each node
    for node in nodes:
        node.metadata = {"title": movie}
    index.insert_nodes(nodes=nodes)

"""#### Auto Retriever Functional Tool

This tool will leverage OpenAI's functional endpoint to select the correct metadata filter and query the filtered index - only looking at nodes with the desired metadata.

A simplified diagram: ![image](https://i.imgur.com/AICDPav.png)

First, we need to create our `VectoreStoreInfo` object which will hold all the relevant metadata we need for each component (in this case title metadata).

Notice that you need to include it in a text list.

Then we will define a retriever `VectorIndexAutoRetriever` which will retrieve relevant info from index.
And finally we will create a retriever_query_engine `RetieverQueryEngine` which will act as us communication point between query and retriever.
"""

from llama_index.core.tools import FunctionTool
from llama_index.core.vector_stores.types import VectorStoreInfo, MetadataInfo
from llama_index.core.retrievers import VectorIndexAutoRetriever
from llama_index.core.query_engine import RetrieverQueryEngine

top_k = 3

vector_store_info = VectorStoreInfo(
    content_info="semantic information about movies",
    metadata_info=[
        MetadataInfo(
            name="title", type="str",
            description="title of the movie, one of [Barbie (film), Oppenheimer (film)]"
        ),
    ],
)
vector_auto_retriever = VectorIndexAutoRetriever(
    index=index, vector_store_info=vector_store_info, similarity_top_k=top_k
)

retriever_query_engine = RetrieverQueryEngine.from_args(
    retriever=vector_auto_retriever
)

"""Here we will define the `QueryEngineTool` for our vector_query_engine which will be provided to the `OpenAIAgent` as a tool"""

from llama_index.core.tools import QueryEngineTool


# vector_tool detailed information
vector_tool_description = f"""
Use this tool to look up semantic information about films.
The vector database schema is given below:
{vector_store_info.json()}
"""
vector_tool = QueryEngineTool.from_defaults(
    query_engine=retriever_query_engine,
    name="vector_tool",
    description=vector_tool_description
)

"""#### Now we will work to create a `QueryEngineTool` for our sql data"""

from llama_index.agent.openai import OpenAIAgent

vector_agent = OpenAIAgent.from_tools(
    tools=[vector_tool]
)

response = vector_agent.chat("Tell me what happens (briefly) in the Barbie movie.")

response

"""### ADDING SQL TO THE AGENT's ARSENAL

The next few steps should be largely straightforward, we'll want to:

1. Read in our `.csv` files into `pd.DataFrame` objects
2. Create an in-memory `sqlite` powered `sqlalchemy` engine
3. Cast our `pd.DataFrame` objects to the SQL engine
4. Create an `SQLDatabase` object through LlamaIndex
5. Use that to create a `QueryEngineTool` that we can interact with through the `NLSQLTableQueryEngine`!
"""

import pandas as pd

barbie_df = pd.read_csv("/content/drive/MyDrive/Colab Notebooks/training_data/barbie.csv")
oppenheimer_df = pd.read_csv("/content/drive/MyDrive/Colab Notebooks/training_data/barbie.csv")

from sqlalchemy import create_engine

engine = create_engine("sqlite://")

barbie_df.to_sql(
    "barbie",
    engine
)

oppenheimer_df.to_sql(
    "oppenheimer",
    engine
)

"""Create SQLDatabase for the sqlengine"""

from llama_index.core import SQLDatabase

sql_database = SQLDatabase(
    engine=engine,
    include_tables=["barbie", "oppenheimer"]
)

"""Create the NLSQLTableQueryEngine interface for all added SQL tables"""

from llama_index.core.query_engine import NLSQLTableQueryEngine

sql_query_engine = NLSQLTableQueryEngine(sql_database=sql_database)

"""Define a QueryEngineTool which will utilize the sql data we put together."""

sql_tool = QueryEngineTool.from_defaults(
    query_engine=sql_query_engine,
    name="sql_tool",
    description=(
        "Useful for translating a natural language query into a SQL query over"
        "barbie, containing information related to reviews of the Barbie movie"
        "oppenheimer, containing information related to reviews of the Oppenheimer movie"
    ),
)

"""Create an Agent"""

sql_agent = OpenAIAgent.from_tools(
    tools=[sql_tool]
)

"""Testing the response from sql tool"""

response = sql_agent.chat("What is the average rating of the two films?")

response

"""### FINALLY COMBINING THE TWO TOOLS"""

barbenheimer_agent = OpenAIAgent.from_tools(
    tools=[sql_tool, vector_tool],
    verbose=True
)

print(str(barbenheimer_agent.chat("What is the lowest rating of the two films - and can you summarize what the reviewer said?")))

response

