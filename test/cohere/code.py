import sys
import oci
import json

def debug(question: str):
    compartment_id = "ocid1.compartment.oc1..aaaaaaaagz67e5f36rxph7l6xuolm7toexy3ylresvl2ijpxefj76db57dvq"
    CONFIG_PROFILE = "DEFAULT"
    config = oci.config.from_file('~/.oci/config', CONFIG_PROFILE)

    endpoint = "https://inference.generativeai.eu-frankfurt-1.oci.oraclecloud.com"

    generative_ai_inference_client = oci.generative_ai_inference.GenerativeAiInferenceClient(config=config, service_endpoint=endpoint, retry_strategy=oci.retry.NoneRetryStrategy(), timeout=(10,240))
    chat_detail = oci.generative_ai_inference.models.ChatDetails()

    chat_request = oci.generative_ai_inference.models.CohereChatRequest()
    chat_request.message = question
    chat_request.max_tokens = 500
    chat_request.temperature = 0
    chat_request.frequency_penalty = 0
    chat_request.top_p = 0
    chat_request.top_k = 0

    chat_detail.serving_mode = oci.generative_ai_inference.models.OnDemandServingMode(model_id="ocid1.generativeaimodel.oc1.eu-frankfurt-1.amaaaaaask7dceyaeamxpkvjhthrqorbgbwlspi564yxfud6igdcdhdu2whq") # Cohere 1.7
    chat_detail.chat_request = chat_request
    chat_detail.compartment_id = compartment_id

    chat_response = generative_ai_inference_client.chat(chat_detail)
    return vars(chat_response)



def answer(question: str):
    compartment_id = "ocid1.compartment.oc1..aaaaaaaagz67e5f36rxph7l6xuolm7toexy3ylresvl2ijpxefj76db57dvq"
    CONFIG_PROFILE = "DEFAULT"
    config = oci.config.from_file('~/.oci/config', CONFIG_PROFILE)

    endpoint = "https://inference.generativeai.eu-frankfurt-1.oci.oraclecloud.com"

    generative_ai_inference_client = oci.generative_ai_inference.GenerativeAiInferenceClient(config=config, service_endpoint=endpoint, retry_strategy=oci.retry.NoneRetryStrategy(), timeout=(10,240))
    chat_detail = oci.generative_ai_inference.models.ChatDetails()

    chat_request = oci.generative_ai_inference.models.CohereChatRequest()
    chat_request.message = question
    chat_request.max_tokens = 500
    chat_request.temperature = 0
    chat_request.frequency_penalty = 0
    chat_request.top_p = 0
    chat_request.top_k = 0

    chat_detail.serving_mode = oci.generative_ai_inference.models.OnDemandServingMode(model_id="ocid1.generativeaimodel.oc1.eu-frankfurt-1.amaaaaaask7dceyaeamxpkvjhthrqorbgbwlspi564yxfud6igdcdhdu2whq") # Cohere 1.7
    chat_detail.chat_request = chat_request
    chat_detail.compartment_id = compartment_id

    chat_response = generative_ai_inference_client.chat(chat_detail)

    return chat_response.data.chat_response.text

