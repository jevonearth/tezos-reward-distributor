import base58
from log_config import main_logger
from util.client_utils import client_list_known_contracts, sign, check_response, send_request
from util.rpc_utils import parse_json_response

logger = main_logger

COMM_HEAD = "{} rpc get http://{}/chains/main/blocks/head"
COMM_PROT = "{} rpc get http://{}/protocols"
COMM_COUNTER = "{} rpc get http://{}/chains/main/blocks/head/context/contracts/{}/counter"
CONTENT = '{"kind":"transaction","source":"%SOURCE%","destination":"%DESTINATION%","fee":"0","counter":"%COUNTER%","gas_limit": "200", "storage_limit": "0","amount":"%AMOUNT%"}'
FORGE_JSON = '{"branch": "%BRANCH%","contents":[%CONTENT%]}'
PREAPPLY_JSON = '[{"protocol":"%PROTOCOL%","branch":"%BRANCH%","contents":[%CONTENT%],"signature":"%SIGNATURE%"}]'
COMM_FORGE = "{} rpc post http://%NODE%/chains/main/blocks/head/helpers/forge/operations with '%JSON%'"
COMM_PREAPPLY = "{} rpc post http://%NODE%/chains/main/blocks/head/helpers/preapply/operations with '%JSON%'"
COMM_INJECT = "{} rpc post http://%NODE%/injection/operation with '\"%OPERATION_HASH%\"'"
COMM_WAIT = "{} wait for %OPERATION% to be included ---confirmations 1"


class BatchPayer():
    def __init__(self, node_url, client_path, key_name):
        super(BatchPayer, self).__init__()
        self.key_name = key_name
        self.node_url = node_url
        self.client_path = client_path

        self.known_contracts = client_list_known_contracts(self.client_path)
        self.source = self.key_name if self.key_name.startswith("KT") or self.key_name.startswith("tz") else \
            self.known_contracts[self.key_name]

        self.comm_head = COMM_HEAD.format(self.client_path, self.node_url)
        self.comm_protocol = COMM_PROT.format(self.client_path, self.node_url)
        self.comm_counter = COMM_COUNTER.format(self.client_path, self.node_url, self.source)
        self.comm_forge = COMM_FORGE.format(self.client_path).replace("%NODE%", self.node_url)
        self.comm_preapply = COMM_PREAPPLY.format(self.client_path).replace("%NODE%", self.node_url)
        self.comm_inject = COMM_INJECT.format(self.client_path).replace("%NODE%", self.node_url)
        self.comm_wait = COMM_WAIT.format(self.client_path)

    def pay(self, payment_items, verbose=None):
        counter = parse_json_response(send_request(self.comm_counter, verbose))
        counter = int(counter)

        head = parse_json_response(send_request(self.comm_head, verbose))
        branch = head["hash"]
        protocol = head["metadata"]["protocol"]

        logger.debug("head: branch {} counter {} protocol {}".format(branch, counter, protocol))

        content_list = []

        for payment_item in payment_items:
            pymnt_addr = payment_item["address"]
            pymnt_amnt = payment_item["payment"]
            pymnt_amnt = int(pymnt_amnt * 1e6)  # expects in micro tezos

            if pymnt_amnt == 0:
                continue

            counter = counter + 1
            content = CONTENT.replace("%SOURCE%", self.source).replace("%DESTINATION%", pymnt_addr) \
                .replace("%AMOUNT%", str(pymnt_amnt)).replace("%COUNTER%", str(counter))
            content_list.append(content)

        contents_string = ",".join(content_list)

        # for the operations
        logger.debug("Forging {} operations".format(len(content_list)))
        forge_json = FORGE_JSON.replace('%BRANCH%', branch).replace("%CONTENT%", contents_string)
        forge_command_str = self.comm_forge.replace("%JSON%", forge_json)
        if verbose: logger.debug("forge_command_str is |{}|".format(forge_command_str))
        forge_command_response = send_request(forge_command_str, verbose)
        if not check_response(forge_command_response):
            logger.error("Error in forge response '{}'".format(forge_command_response))
            return False

        # sign the operations
        bytes = parse_json_response(forge_command_response)
        signed_bytes = sign(self.client_path, bytes, self.key_name)

        # pre-apply operations
        logger.debug("Preapplying the operations")
        preapply_json = PREAPPLY_JSON.replace('%BRANCH%', branch).replace("%CONTENT%", contents_string).replace(
            "%PROTOCOL%", protocol).replace("%SIGNATURE%", signed_bytes)
        preapply_command_str = self.comm_preapply.replace("%JSON%", preapply_json)

        if verbose: logger.debug("preapply_command_str is |{}|".format(preapply_command_str))
        preapply_command_response = send_request(preapply_command_str, verbose)
        if not check_response(preapply_command_response):
            logger.error("Error in preapply response '{}'".format(preapply_command_response))
            return False

        # not necessary
        # preapplied = parse_response(preapply_command_response)

        # inject the operations
        logger.debug("Injecting {} operations".format(len(content_list)))
        decoded = base58.b58decode(signed_bytes).hex()
        decoded_edsig_signature = decoded.replace("09f5cd8612", "")[:-8]
        signed_operation_bytes = bytes + decoded_edsig_signature
        inject_command_str = self.comm_inject.replace("%OPERATION_HASH%", signed_operation_bytes)
        if verbose: logger.debug("inject_command_str is |{}|".format(inject_command_str))
        inject_command_response = send_request(inject_command_str, verbose)
        if not check_response(inject_command_response):
            logger.error("Error in inject response '{}'".format(inject_command_response))
            return False

        operation_hash = parse_json_response(inject_command_response)
        logger.debug("Operation hash is {}".format(operation_hash))

        # wait for inclusion
        logger.debug("Waiting for operation {} to be included".format(operation_hash))
        send_request(self.comm_wait.replace("%OPERATION%", operation_hash), verbose)
        logger.debug("Operation {} is included".format(operation_hash))

        return True


if __name__ == '__main__':
    payer = BatchPayer("127.0.0.1:8273", "~/zeronet.sh client", "mybaker")