
import { LightningElement } from 'lwc';

export default class LwcNestedAtTheEnd extends LightningElement {
    handleButtonClick() {
        this.dispatchEvent(
            new CustomEvent('bottomiscalling',
                {
                    detail: {data: 'ping'},
                    bubbles: false,
                    composed: false
                }
            )
        );
    }
}