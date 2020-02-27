
import { LightningElement, track, wire} from 'lwc';
import { publish, APPLICATION_SCOPE, releaseMessageContext, subscribe, unsubscribe, MessageContext } from 'lightning/messageService';
import DEMOMESSAGE from "@salesforce/messageChannel/DemoMessage__c";

export default class Lms_LWC_Demo extends LightningElement {
    @track myMessage;
    @track receivedMessage;

    @wire(MessageContext) messageContext;

    channel;

    handleSubscribe() {
        this.channel = subscribe(this.messageContext, DEMOMESSAGE, (event) => {
            if (event != null) {
                const message = event.messageToSend;
                const source = event.sourceSystem;

                this.receivedMessage = 'Message: ' + message + '. Sent From: ' + source;
            }
        }, {scope: APPLICATION_SCOPE});
    }

    handleMessage(event){
         if (event != null) {
             const message = event.messageToSend;
             const source = event.sourceSystem;

             parentPage.receivedMessage = 'Message: ' + message + '. Sent From: ' + source;
         }
     }

    handleUnsubscribe() {
        unsubscribe(this.channel);
    }

    handleChange(event) {
        this.myMessage = event.target.value;
    }

    handleClick() {
        this.publish();
    }

    publish() {
        const payload = {
            sourceSystem: "lwc",
            messageToSend: this.myMessage
        };

        publish(this.messageContext, DEMOMESSAGE, payload);
    }

    disconnectedCallback() {
        releaseMessageContext(this.messageContext);
    }
}